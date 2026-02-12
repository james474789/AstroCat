import os
import httpx
import json
import logging
from PIL import Image as PILImage
from pathlib import Path
from app.config import settings

logger = logging.getLogger(__name__)


# Default to Nova, but allow override
NOVA_API_URL = "http://nova.astrometry.net/api"

class AstrometryService:
    @staticmethod
    async def get_session_key(api_key: str, base_url: str = NOVA_API_URL) -> str:
        """Login and get session key."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/login",
                data={"request-json": json.dumps({"apikey": api_key})}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "success":
                raise ValueError(f"Astrometry login failed: {data.get('message')}")
            return data["session"]

    @staticmethod
    async def upload_file(file_path: str, api_key: str, base_url: str = NOVA_API_URL, hints: dict = None) -> dict:
        """
        Convert image to JPG (if needed) and upload to Astrometry.net.
        Returns the submission dictionary (including 'subid').
        
        hints: Optional dictionary of hints for the solver (e.g. center_ra, center_dec, radius, scale_units, etc.)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Ensure we have a session key (optimization: cache this later if needed)
        session_key = await AstrometryService.get_session_key(api_key, base_url)

        # Temp file for upload
        # Source directory might be read-only, so use /tmp
        import tempfile
        temp_jpg = Path(tempfile.gettempdir()) / f"{path.stem}_{os.urandom(4).hex()}.jpg"
        
        try:
            # Use the existing ThumbnailGenerator to load FITS, RAW, or standard images
            from app.services.thumbnails import ThumbnailGenerator
            img = ThumbnailGenerator.load_source_image(str(path), is_subframe=False)
            
            if not img:
                raise ValueError(f"Failed to load image for conversion: {file_path}")
            
            # Resize for Astrometry.net (max 2000px on long side is more than enough for solving)
            max_size = 2000
            if max(img.width, img.height) > max_size:
                ratio = max_size / max(img.width, img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, PILImage.Resampling.LANCZOS)
                logger.info(f"[ASTROMETRY] Resized image to {new_size} for Astrometry upload")
            
            # Save as JPG for upload
            # Strip metadata to avoid save errors (e.g. malformed XMP which causes "can't concat tuple to bytes")
            img.info = {}
            img.save(temp_jpg, "JPEG", quality=85)

            # Upload
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(temp_jpg, "rb") as f:
                    # Construct multipart payload
                    # The API expects 'request-json' field effectively as a string, AND file.
                    
                    req_data = {
                        "publicly_visible": "n",
                        "allow_commercial_use": "d", 
                        "allow_modifications": "d",
                        "session": session_key,
                    }

                    # If using the remote astrometry server, do not include hints to allow a fully blind solve.
                    # Otherwise (local solver), apply default scale and any provided hints.
                    if "nova.astrometry.net" not in base_url:
                        req_data.update({
                            "scale_units": "degwidth", 
                            "scale_lower": 0.1,
                            "scale_upper": 180.0,
                        })
                        if hints:
                            req_data.update(hints)
                    else:
                         logger.info("[ASTROMETRY] Uploading to remote server. Skipping all hints for a fully blind solve.")
                    
                    # Log the full request data (excluding session for security)
                    log_data = req_data.copy()
                    if "session" in log_data:
                        log_data["session"] = "REDACTED"
                    
                    # print(f"DEBUG: Submitting to Astrometry with payload: {log_data}", flush=True)
                    logger.info(f"[ASTROMETRY] Submitting to Astrometry ({base_url}) with payload: {log_data}")
                    
                    files = {"file": (path.name, f, "image/jpeg")}
                    
                    resp = await client.post(
                        f"{base_url}/upload",
                        data={"request-json": json.dumps(req_data)},
                        files=files
                    )
                    resp.raise_for_status()
                    return resp.json()

        except Exception as e:
            logger.error(f"[ASTROMETRY] Failed to upload to Astrometry: {e}")
            raise
        finally:
            # Cleanup temp file
            if temp_jpg.exists():
                temp_jpg.unlink()

    @staticmethod
    async def get_submission_status(sub_id: str | int, base_url: str = NOVA_API_URL) -> dict:
        """Check submission status. Returns {processing_finished: bool, jobs: [job_id, ...], ...}"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/submissions/{sub_id}")
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def get_job_status(job_id: str | int, base_url: str = NOVA_API_URL) -> dict:
        """Get job status. Returns {status: 'success'|'failure', ...}"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/jobs/{job_id}")
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def get_calibration(job_id: str | int, base_url: str = NOVA_API_URL) -> dict:
        """Get calibration results (WCS)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/jobs/{job_id}/calibration")
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def get_tags(job_id: str | int, base_url: str = NOVA_API_URL) -> list:
        """Get objects found."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/jobs/{job_id}/tags")
            # This endpoint sometimes returns list directly?
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def download_annotated_image(job_id: str | int, output_path: str, base_url: str = NOVA_API_URL):
        """
        Download the annotated image for a given job ID.
        For Nova, URL is: http://nova.astrometry.net/annotated_display/{job_id}
        """
        # Construct URL.
        # If base_url is "http://nova.astrometry.net/api", we want "https://nova.astrometry.net"
        if "nova.astrometry.net" in base_url:
            root_url = "https://nova.astrometry.net"
        else:
            root_url = base_url.replace("/api", "")
            
        url = f"{root_url}/annotated_display/{job_id}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0"
        }
        
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            # 1. Establish session by hitting homepage
            if "nova.astrometry.net" in root_url:
                try:
                    logger.debug("[ASTROMETRY] Establishing Astrometry.net session...")
                    await client.get(f"{root_url}/")
                except Exception as e:
                    logger.warning(f"Homepage session hit failed: {e}")

            # 2. Try to login via API if we have a key
            if "nova.astrometry.net" in root_url and settings.astrometry_api_key:
                try:
                    login_url = f"{root_url}/api/login"
                    login_data = {"request-json": json.dumps({"apikey": settings.astrometry_api_key})}
                    login_resp = await client.post(login_url, data=login_data)
                    if login_resp.status_code == 200:
                         data = login_resp.json()
                         if data.get("status") == "success":
                             logger.debug(f"API Login successful for job {job_id}")
                except Exception as e:
                    logger.warning(f"API Login failed during annotation fetch (ignoring): {e}")

            # 3. Request actual image
            # Set referer to homepage to look more legitimate
            client.headers["Referer"] = f"{root_url}/"
            logger.info(f"[ASTROMETRY] Downloading annotation from {url}...")
            resp = await client.get(url)
            logger.info(f"[ASTROMETRY] Requested annotated image: {resp.status_code} {resp.url}")
            resp.raise_for_status()
            
            # Check for human verification check
            content_type = resp.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                 # Check for various markers of the human check page
                 is_human_check = "ask_human" in str(resp.url) or \
                                 "iocaine" in str(resp.url) or \
                                 "Human check" in resp.text or \
                                 "am_human" in resp.text
                                 
                 if is_human_check:
                     logger.info(f"Human verification required at {resp.url}, attempting bypass...")
                     import re
                     
                     # Try to get CSRF token from cookies first, then from form
                     csrf_token = resp.cookies.get("csrftoken")
                     if not csrf_token:
                         csrf_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', resp.text)
                         if csrf_match:
                             csrf_token = csrf_match.group(1)
                     
                     if csrf_token:
                         next_val = f"/annotated_display/{job_id}"
                         next_match = re.search(r'name="next" value="([^"]+)"', resp.text)
                         if next_match:
                             next_val = next_match.group(1)
                         
                         post_data = {
                             "csrfmiddlewaretoken": csrf_token,
                             "human": "yup",
                             "next": next_val
                         }
                         
                         am_human_url = f"{root_url}/am_human"
                         logger.info(f"Posting bypass to {am_human_url}")
                         
                         bypass_headers = headers.copy()
                         bypass_headers["Referer"] = str(resp.url)
                         
                         resp = await client.post(am_human_url, data=post_data, headers=bypass_headers)
                         
                         if "image" not in resp.headers.get("content-type", "").lower():
                             logger.info("Bypass sent, retrying image download...")
                             resp = await client.get(url)
                     else:
                         logger.warning("Could not find CSRF token for human check bypass.")
            
            # Verify final result is an image
            final_content_type = resp.headers.get("content-type", "").lower()
            if "image" not in final_content_type:
                 logger.error(f"Final download failed. Status: {resp.status_code}, Type: {final_content_type}, URL: {resp.url}")
                 if "text/html" in final_content_type:
                     if "ask_human" in str(resp.url):
                         raise ValueError(f"Astrometry.net requested human verification (ask_human). Bypass failed.")
                     raise ValueError(f"Received HTML instead of image from {resp.url}. Likely redirected to login or error page.")
                 raise ValueError(f"Response content-type is {final_content_type}, expected image.")
            # Success - save the file
            with open(output_path, "wb") as f:
                f.write(resp.content)
            
    @staticmethod
    async def get_wcs_file(job_id: str | int, base_url: str = NOVA_API_URL) -> dict:
        """
        Download and parse the WCS header for a given job ID.
        Returns a dictionary of the FITS header cards.
        """
        # Construct URL.
        if "nova.astrometry.net" in base_url:
            root_url = "https://nova.astrometry.net"
        else:
            root_url = base_url.replace("/api", "")
            
        url = f"{root_url}/wcs_file/{job_id}"
        
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            
            # Parse FITS in memory
            from astropy.io import fits
            import io
            
            with fits.open(io.BytesIO(resp.content)) as hdul:
                # The WCS is usually in the primary header
                header = hdul[0].header
                
                # Convert to dict, filtering out non-serializable or irrelevant items
                header_dict = {}
                for k, v in header.items():
                    if k in ['HISTORY', 'COMMENT', 'ExifOffset']:
                        continue
                    # Ensure values are JSON serializable
                    if isinstance(v, (str, int, float, bool)):
                         header_dict[k] = v
                    else:
                         header_dict[k] = str(v)
                         
                return header_dict
