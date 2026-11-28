"""
Helper utilities for the /library catalog explorer.
Provides fast aggregated stats, paginated list queries with rich metadata tags,
and full single-item inspection with playable/stremio links.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import PTN

from Backend import db
from Backend.helper.metadata import resolve_cover_url
from Backend.helper.settings_manager import SettingsManager
from Backend.logger import LOGGER

# In-memory short-lived cache for ledger stats (5 minutes)
_STATS_CACHE: Dict[str, Any] = {"data": None, "expires": 0}

# In-memory LRU-style cache for library query pages (TTL: 3 minutes)
_QUERY_CACHE: Dict[str, tuple[float, dict]] = {}
_ITEM_CACHE: Dict[str, tuple[float, dict]] = {}
_CACHE_MAX_ENTRIES = 300
_CACHE_TTL = 180  # 3 minutes


def clear_library_cache() -> None:
    """Invalidates all in-memory caches for the library ledger and queries."""
    global _STATS_CACHE, _QUERY_CACHE, _ITEM_CACHE
    _STATS_CACHE = {"data": None, "expires": 0}
    _QUERY_CACHE.clear()
    _ITEM_CACHE.clear()


def _get_from_cache(cache: dict, key: str) -> Optional[dict]:
    now = time.time()
    if key in cache:
        exp, val = cache[key]
        if now < exp:
            return val
        del cache[key]
    return None


def _set_in_cache(cache: dict, key: str, val: dict, ttl: int = _CACHE_TTL) -> None:
    if len(cache) >= _CACHE_MAX_ENTRIES:
        # Prune oldest 20%
        oldest = sorted(cache.keys(), key=lambda k: cache[k][0])[: _CACHE_MAX_ENTRIES // 5]
        for k in oldest:
            cache.pop(k, None)
    cache[key] = (time.time() + ttl, val)


def parse_filename_meta(filename: str) -> Dict[str, Any]:
    """Parse resolution, codec, audio, source, and languages from video filename."""
    if not filename:
        return {
            "res": "",
            "codec": "",
            "quality": "",
            "audio": "",
            "langs": [],
        }

    p = PTN.parse(filename)
    res = str(p.get("resolution") or "")
    fl = filename.lower()
    if not res:
        if "2160p" in fl or "4k" in fl or "uhd" in fl or "ds4k" in fl:
            res = "2160p"
        elif "1080p" in fl:
            res = "1080p"
        elif "720p" in fl:
            res = "720p"
        elif "480p" in fl:
            res = "480p"

    codec = str(p.get("codec") or "")
    if not codec:
        if "x265" in fl or "hevc" in fl or "h265" in fl or "h.265" in fl:
            codec = "HEVC"
        elif "x264" in fl or "h264" in fl or "avc" in fl or "h.264" in fl:
            codec = "H.264"
        elif "av1" in fl:
            codec = "AV1"

    quality = str(p.get("quality") or "")
    if not quality:
        if "web-dl" in fl or "webdl" in fl:
            quality = "WEB-DL"
        elif "webr" in fl:
            quality = "WEBRip"
        elif "bluray" in fl or "bdrip" in fl:
            quality = "BluRay"

    audio = str(p.get("audio") or "")
    if not audio:
        if "atmos" in fl:
            audio = "Atmos"
        elif "ddp" in fl or "eac3" in fl:
            audio = "DDP"
        elif "ac3" in fl or "dd" in fl:
            audio = "DD"
        elif "dts" in fl:
            audio = "DTS"
        elif "aac" in fl:
            audio = "AAC"

    langs: List[str] = []
    lang = p.get("language")
    if isinstance(lang, str) and lang:
        langs.append(lang.capitalize())
    elif isinstance(lang, list):
        for l in lang:
            if isinstance(l, str) and l:
                langs.append(l.capitalize())

    for l_key, l_val in [
        ("hindi", "Hindi"),
        ("tamil", "Tamil"),
        ("telugu", "Telugu"),
        ("kannada", "Kannada"),
        ("malayalam", "Malayalam"),
        ("english", "English"),
        ("dual", "Dual Audio"),
        ("multi", "Multi Audio"),
    ]:
        if l_key in fl and l_val not in langs:
            langs.append(l_val)

    return {
        "res": res,
        "codec": codec,
        "quality": quality,
        "audio": audio,
        "langs": langs,
    }


def extract_media_summary(doc: dict, media_type: str) -> dict:
    """Extract card representation including quality tags, language badges, and file counts."""
    tmdb_id = doc.get("tmdb_id")
    db_index = doc.get("db_index", 1)
    imdb_id = doc.get("imdb_id") or ""
    title = doc.get("title_english") or doc.get("title") or "Unknown"
    release_year = doc.get("release_year")
    poster = resolve_cover_url(doc.get("poster") or "")
    backdrop = resolve_cover_url(doc.get("backdrop") or "")
    rating = doc.get("rating")
    genres = doc.get("genres") or []
    orig_lang = (doc.get("original_language") or "").strip().lower()

    has_uhd = False
    file_count = 0
    season_count = 0
    episodes_count = 0

    if media_type == "movie":
        telegram_files = doc.get("telegram") or []
        file_count = len(telegram_files)
        for f in telegram_files:
            fname = str(f.get("name") or "")
            fqual = str(f.get("quality") or "").lower()
            fl = fname.lower()
            if (
                "2160p" in fqual
                or "4k" in fqual
                or "uhd" in fqual
                or "2160p" in fl
                or "4k" in fl
                or "uhd" in fl
            ):
                has_uhd = True
    else:
        seasons = doc.get("seasons") or []
        season_count = len(seasons)
        for s in seasons:
            for ep in (s.get("episodes") or []):
                episodes_count += 1
                for f in (ep.get("telegram") or []):
                    file_count += 1
                    fname = str(f.get("name") or "")
                    fqual = str(f.get("quality") or "").lower()
                    fl = fname.lower()
                    if (
                        "2160p" in fqual
                        or "4k" in fqual
                        or "uhd" in fqual
                        or "2160p" in fl
                        or "4k" in fl
                        or "uhd" in fl
                    ):
                        has_uhd = True

    key = f"{media_type}-{db_index}-{tmdb_id}"
    return {
        "key": key,
        "type": media_type,
        "tmdb_id": tmdb_id,
        "db_index": db_index,
        "imdb_id": imdb_id,
        "title": title,
        "release_year": release_year,
        "poster": poster,
        "backdrop": backdrop,
        "rating": rating,
        "genres": genres[:3] if genres else [],
        "files": file_count,
        "original_language": orig_lang,
        "uhd": has_uhd,
        "seasons": season_count if media_type in ("tv", "series") else None,
        "episodes": episodes_count if media_type in ("tv", "series") else None,
    }


async def get_library_stats() -> dict:
    """Calculates or fetches cached stats for the ledger bar."""
    now = time.time()
    if _STATS_CACHE["data"] and now < _STATS_CACHE["expires"]:
        return _STATS_CACHE["data"]

    movies_count = 0
    shows_count = 0
    storage_keys = [k for k in db.dbs.keys() if k.startswith("storage_")]

    for k in storage_keys:
        try:
            m_c = await db.dbs[k]["movie"].count_documents({"telegram.0": {"$exists": True}})
            s_c = await db.dbs[k]["tv"].count_documents({"seasons.episodes.telegram.0": {"$exists": True}})
            movies_count += m_c
            shows_count += s_c
        except Exception as e:
            LOGGER.debug(f"[Library] Error counting docs in {k}: {e}")

    # Count active tokens / users safely matching admin/access logic
    active_users = 0
    try:
        from datetime import datetime
        now_dt = datetime.utcnow()
        sub_on = bool(SettingsManager.current().subscription)
        tokens = await db.get_all_api_tokens()

        # Map subscribers for rapid subscription checking
        subscriber_map = {}
        if sub_on:
            try:
                for u in await db.get_all_subscribers():
                    subscriber_map[str(u.get("_id"))] = u
            except Exception:
                pass

        seen_user_ids = set()

        for t in tokens:
            token_user_id = t.get("user_id")
            uid_str = str(token_user_id) if token_user_id is not None else None
            user = None
            if uid_str:
                seen_user_ids.add(uid_str)
                user = subscriber_map.get(uid_str)
                if not user:
                    try:
                        user = await db.get_user(int(token_user_id))
                    except Exception:
                        pass

            is_admin = bool(t.get("is_admin")) or (uid_str and db._is_owner(int(token_user_id)))
            lifetime = bool(t.get("subscription_exempt"))
            token_expiry = t.get("expires_at")
            user_sub_expiry = user.get("subscription_expiry") if user else None
            sub_status = user.get("subscription_status") if user else None

            if not sub_on:
                is_expired = False
            elif is_admin or lifetime:
                is_expired = False
            elif token_expiry is not None:
                # token_expiry can be datetime or timestamp
                exp_dt = datetime.fromtimestamp(token_expiry) if isinstance(token_expiry, (int, float)) else token_expiry
                is_expired = exp_dt < now_dt
            elif user and sub_status == "active" and user_sub_expiry:
                exp_dt = datetime.fromtimestamp(user_sub_expiry) if isinstance(user_sub_expiry, (int, float)) else user_sub_expiry
                is_expired = exp_dt < now_dt
            else:
                is_expired = True

            if not is_expired:
                active_users += 1

        # Also count subscribers who don't have a token doc yet but have active sub
        if sub_on:
            for uid_str, u in subscriber_map.items():
                if uid_str not in seen_user_ids:
                    if db.is_subscription_active(u, now_dt):
                        active_users += 1

    except Exception as e:
        LOGGER.debug(f"[Library] Error counting active users: {e}")

    stats = {
        "movies": movies_count,
        "shows": shows_count,
        "total_titles": movies_count + shows_count,
        "active_users": max(active_users, 1),
        "updated_at": int(now),
    }

    _STATS_CACHE["data"] = stats
    _STATS_CACHE["expires"] = now + 300  # 5 minutes
    return stats


def _build_db_filters(filter_tag: str) -> tuple[dict, dict]:
    """Construct MongoDB filters based on TMDB/IMDb original_language and 4K quality."""
    movie_f: dict = {"telegram.0": {"$exists": True}}
    tv_f: dict = {"seasons.episodes.telegram.0": {"$exists": True}}

    if filter_tag == "hindi":
        movie_f["original_language"] = "hi"
        tv_f["original_language"] = "hi"
    elif filter_tag == "english":
        movie_f["original_language"] = "en"
        tv_f["original_language"] = "en"
    elif filter_tag == "uhd":
        uhd_regex = {"$regex": "2160p|4k|uhd", "$options": "i"}
        movie_f["$or"] = [
            {"telegram.quality": uhd_regex},
            {"telegram.name": uhd_regex},
        ]
        tv_f["$or"] = [
            {"seasons.episodes.telegram.quality": uhd_regex},
            {"seasons.episodes.telegram.name": uhd_regex},
        ]

    return movie_f, tv_f


async def query_library_list(
    media_type: str = "all",
    filter_tag: str = "all",
    sort_by: str = "recent",
    query: str = "",
    page: int = 1,
    page_size: int = 36,
) -> dict:
    """
    Paginated search and filter query across movies and tv shows in TeleStremio.
    Supports in-memory caching for lightning fast pagination & filter switches.
    """
    cache_key = f"{media_type}:{filter_tag}:{sort_by}:{query}:{page}:{page_size}"
    cached = _get_from_cache(_QUERY_CACHE, cache_key)
    if cached:
        return cached

    movie_filter, tv_filter = _build_db_filters(filter_tag)

    # 1. Search Query Branch
    if query and query.strip():
        q = query.strip()
        m_type = None if media_type == "all" else ("tv" if media_type in ("tv", "series") else "movie")
        extra_filter = tv_filter if m_type == "tv" else (movie_filter if m_type == "movie" else None)

        search_res = await db.search_documents(
            query=q,
            page=page,
            page_size=page_size,
            extra_filter=extra_filter,
            media_type=m_type,
        )
        raw_items = search_res.get("results") or []
        total = search_res.get("total_count", 0)

        items = []
        for doc in raw_items:
            m_t = doc.get("media_type") or ("tv" if "seasons" in doc else "movie")
            summary = extract_media_summary(doc, m_t)
            if not summary["files"]:
                continue
            # Post-filter validation for TMDB original_language or UHD
            if filter_tag == "hindi" and summary["original_language"] != "hi":
                continue
            if filter_tag == "english" and summary["original_language"] != "en":
                continue
            if filter_tag == "uhd" and not summary["uhd"]:
                continue
            items.append(summary)

        stats = await get_library_stats()
        out = {
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "items": items,
            "stats": stats,
        }
        _set_in_cache(_QUERY_CACHE, cache_key, out)
        return out

    # 2. Browse / Paginated Sort Branch
    if sort_by == "title":
        sort_params = [("title", "asc")]
    elif sort_by == "year":
        sort_params = [("release_year", "desc")]
    else:
        sort_params = [("updated_on", "desc")]

    items = []
    total = 0

    if media_type == "movie":
        res = await db.sort_movies(sort_params, page, page_size, extra_filter=movie_filter)
        total = res.get("total_count", 0)
        for doc in res.get("movies", []):
            summary = extract_media_summary(doc, "movie")
            if not summary["files"]:
                continue
            items.append(summary)
    elif media_type in ("tv", "series"):
        res = await db.sort_tv_shows(sort_params, page, page_size, extra_filter=tv_filter)
        total = res.get("total_count", 0)
        for doc in res.get("tv_shows", []):
            summary = extract_media_summary(doc, "tv")
            if not summary["files"]:
                continue
            items.append(summary)
    else:
        half = max(1, page_size // 2)
        m_res = await db.sort_movies(sort_params, page, half, extra_filter=movie_filter)
        t_res = await db.sort_tv_shows(sort_params, page, half, extra_filter=tv_filter)
        total = m_res.get("total_count", 0) + t_res.get("total_count", 0)

        for doc in m_res.get("movies", []):
            summary = extract_media_summary(doc, "movie")
            if not summary["files"]:
                continue
            items.append(summary)

        for doc in t_res.get("tv_shows", []):
            summary = extract_media_summary(doc, "tv")
            if not summary["files"]:
                continue
            items.append(summary)

    stats = await get_library_stats()
    out = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "items": items,
        "stats": stats,
    }
    _set_in_cache(_QUERY_CACHE, cache_key, out)
    return out


async def get_library_item_details(key: str) -> Optional[dict]:
    """
    Fetch comprehensive item detail for modal preview, including seasons,
    episodes, file codecs, stream urls, and deep links.
    key format: 'movie-{db_index}-{tmdb_id}' or 'tv-{db_index}-{tmdb_id}'
    """
    cached = _get_from_cache(_ITEM_CACHE, key)
    if cached:
        return cached

    parts = key.split("-")
    if len(parts) < 3:
        return None

    media_type = parts[0]
    try:
        db_index = int(parts[1])
        tmdb_id = int(parts[2])
    except ValueError:
        return None

    db_key = f"storage_{db_index}"
    if db_key not in db.dbs:
        return None

    col_name = "tv" if media_type in ("tv", "series") else "movie"
    doc = await db.dbs[db_key][col_name].find_one({"tmdb_id": tmdb_id})
    if not doc:
        return None

    title = doc.get("title_english") or doc.get("title") or "Unknown"
    imdb_id = doc.get("imdb_id") or ""
    poster = resolve_cover_url(doc.get("poster") or "")
    backdrop = resolve_cover_url(doc.get("backdrop") or "")

    stremio_type = "series" if col_name == "tv" else "movie"
    stremio_id = imdb_id or f"tmdb:{tmdb_id}"
    stremio_open_url = f"/open/stremio/{col_name}/{stremio_id}"
    nuvio_open_url = f"/open/nuvio/{col_name}/{stremio_id}"
    stremio_web_url = f"https://web.stremio.com/#/detail/{stremio_type}/{stremio_id}"

    result = {
        "key": key,
        "type": col_name,
        "tmdb_id": tmdb_id,
        "db_index": db_index,
        "imdb_id": imdb_id,
        "title": title,
        "original_title": doc.get("original_title"),
        "original_language": doc.get("original_language"),
        "release_year": doc.get("release_year"),
        "poster": poster,
        "backdrop": backdrop,
        "description": doc.get("description") or doc.get("overview") or "",
        "genres": doc.get("genres") or [],
        "rating": doc.get("rating"),
        "logo": resolve_cover_url(doc.get("logo") or ""),
        "runtime": doc.get("runtime"),
        "stremio_url": stremio_open_url,
        "nuvio_url": nuvio_open_url,
        "stremio_web_url": stremio_web_url,
        "imdb_url": f"https://www.imdb.com/title/{imdb_id}" if imdb_id else None,
        "tmdb_url": f"https://www.themoviedb.org/{'tv' if col_name == 'tv' else 'movie'}/{tmdb_id}",
    }

    if col_name == "movie":
        files = []
        for f in (doc.get("telegram") or []):
            fname = f.get("name") or f"{title}.mkv"
            meta = parse_filename_meta(fname)
            res_val = meta["res"] or f.get("quality") or "HD"
            files.append({
                "name": fname,
                "res": res_val,
                "codec": meta["codec"],
                "quality": meta["quality"],
                "audio": meta["audio"],
                "langs": meta["langs"],
            })
        result["files"] = files
    else:
        seasons_data = []
        for s in (doc.get("seasons") or []):
            sn = s.get("season_number") or 0
            episodes_data = []
            for ep in (s.get("episodes") or []):
                en = ep.get("episode_number") or 0
                ep_files = []
                for f in (ep.get("telegram") or []):
                    fname = f.get("name") or f"Episode {en}.mkv"
                    meta = parse_filename_meta(fname)
                    res_val = meta["res"] or f.get("quality") or "HD"
                    ep_files.append({
                        "name": fname,
                        "res": res_val,
                        "codec": meta["codec"],
                        "quality": meta["quality"],
                        "audio": meta["audio"],
                        "langs": meta["langs"],
                    })
                episodes_data.append({
                    "n": en,
                    "title": ep.get("title") or f"Episode {en}",
                    "released": ep.get("released") or "",
                    "backdrop": resolve_cover_url(ep.get("episode_backdrop") or ""),
                    "overview": ep.get("overview") or "",
                    "files": ep_files,
                })
            seasons_data.append({
                "n": sn,
                "title": s.get("title") or f"Season {sn}",
                "episodes": episodes_data,
            })
        result["seasons"] = seasons_data

    _set_in_cache(_ITEM_CACHE, key, result, ttl=300)
    return result
