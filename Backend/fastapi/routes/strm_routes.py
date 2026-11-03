"""
FastAPI routes for Emby / Jellyfin .strm export.

Endpoints:
  GET /api/strm/export.zip        - Download all .strm + .nfo files as a single zip.
  POST /api/strm/sync             - Trigger sync to local disk (configured in STRM_EXPORT_DIR).
  GET /api/strm/status           - Check STRM configuration and disk sync status.
"""

from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from Backend.fastapi.security.tokens import verify_token
from Backend.fastapi.security.credentials import require_auth
from Backend.helper.settings_manager import SettingsManager
from Backend.helper.strm_generator import STRMExporter, resolve_strm_token
from Backend.logger import LOGGER

router = APIRouter(prefix="/api/strm", tags=["STRM & Emby"])


@router.get("/status")
async def strm_status_api(_: bool = Depends(require_auth)):
    settings = SettingsManager.current()
    export_dir = settings.strm_export_dir
    dir_exists = os.path.isdir(export_dir) if export_dir else False
    strm_count = 0
    nfo_count = 0

    if dir_exists:
        try:
            for root, _, files in os.walk(export_dir):
                for f in files:
                    if f.endswith(".strm"):
                        strm_count += 1
                    elif f.endswith(".nfo"):
                        nfo_count += 1
        except Exception as e:
            LOGGER.warning(f"[STRM] Failed counting files in {export_dir}: {e}")

    return {
        "export_dir": export_dir,
        "dir_exists": dir_exists,
        "default_token_configured": bool(settings.strm_default_token),
        "strm_count": strm_count,
        "nfo_count": nfo_count,
    }


@router.post("/sync")
async def strm_sync_api(_: bool = Depends(require_auth)):
    settings = SettingsManager.current()
    export_dir = settings.strm_export_dir
    if not export_dir:
        raise HTTPException(
            status_code=400,
            detail="STRM Export Directory is not configured. Set it in Settings -> Emby / STRM Support.",
        )

    token = await resolve_strm_token()
    exporter = STRMExporter(token=token, include_nfo=True)
    try:
        result = await exporter.export_to_directory(export_dir)
        return result
    except Exception as e:
        LOGGER.error(f"[STRM] Sync failed: {e}")
        raise HTTPException(status_code=500, detail=f"STRM sync failed: {str(e)}")


@router.get("/export.zip")
async def strm_export_zip_api(
    token: str = Query(None, description="API token to embed inside the .strm files"),
    include_nfo: bool = Query(True, description="Include companion .nfo metadata files"),
):
    resolved_token = await resolve_strm_token(token)
    exporter = STRMExporter(token=resolved_token, include_nfo=include_nfo)

    try:
        zip_buf = await exporter.create_zip_archive()
        headers = {
            "Content-Disposition": 'attachment; filename="telestream_emby_strm.zip"',
            "Content-Type": "application/zip",
        }
        return Response(content=zip_buf.getvalue(), media_type="application/zip", headers=headers)
    except Exception as e:
        LOGGER.error(f"[STRM] Zip export failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate STRM zip: {str(e)}")
