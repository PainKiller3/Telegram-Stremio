import aiohttp
from typing import Optional, Dict, Any
from Backend.helper.settings_manager import SettingsManager
from Backend.logger import LOGGER


def get_jellyfin_base_url() -> str:
    settings = SettingsManager.current()
    return (settings.emby_url or "http://localhost:8096").rstrip("/")


def get_jellyfin_public_url() -> str:
    settings = SettingsManager.current()
    return (settings.emby_public_url or get_jellyfin_base_url()).rstrip("/")


def get_jellyfin_client_headers() -> Optional[Dict[str, str]]:
    settings = SettingsManager.current()
    api_key = settings.emby_api_key
    if not api_key:
        return None
    return {
        "X-Emby-Token": api_key,
        "Authorization": f'MediaBrowser Token="{api_key}"',
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def find_jellyfin_user(username: str) -> Optional[Dict[str, Any]]:
    """Look up a user in Jellyfin by Name (case-insensitive)."""
    headers = get_jellyfin_client_headers()
    if not headers:
        LOGGER.warning("[Jellyfin API] Emby/Jellyfin API key is not configured.")
        return None

    base_url = get_jellyfin_base_url()
    clean_target = username.strip().lower()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/Users", headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    LOGGER.error(f"[Jellyfin API] Failed to list users ({resp.status}): {err_txt}")
                    return None
                users = await resp.json()
                for u in users:
                    if (u.get("Name") or "").strip().lower() == clean_target:
                        return u
    except Exception as e:
        LOGGER.error(f"[Jellyfin API] Error fetching user '{username}': {e}")
    return None


async def reset_jellyfin_user_password(user_id: str, new_password: str) -> bool:
    """Update a Jellyfin user's password using the admin API key."""
    headers = get_jellyfin_client_headers()
    if not headers:
        return False

    base_url = get_jellyfin_base_url()
    pwd_url = f"{base_url}/Users/{user_id}/Password"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                pwd_url,
                json={"NewPw": new_password, "ResetPassword": False},
                headers=headers,
                timeout=10
            ) as resp:
                if resp.status in (200, 204):
                    return True
                err_text = await resp.text()
                LOGGER.error(f"[Jellyfin API] Password update failed ({resp.status}): {err_text}")
    except Exception as e:
        LOGGER.error(f"[Jellyfin API] Error updating password: {e}")
    return False
