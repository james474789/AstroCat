import os
import numpy as np
import logging
from PIL import Image, ImageOps
from pathlib import Path

logger = logging.getLogger(__name__)

# Try importing specialized libraries
try:
    import rawpy
except ImportError:
    rawpy = None

try:
    from astropy.io import fits
except ImportError:
    fits = None

try:
    import xisf
except ImportError:
    xisf = None

try:
    import tifffile
except ImportError:
    tifffile = None


class ThumbnailGenerator:

    @staticmethod
    def apply_stf_stretch(data, target_bg=0.25, shadows_clip=-1.25):
        """
        Apply PixInsight-style STF Auto Stretch to image data (numpy array).
        Expects floating point input (normalized or not, will be normalized).
        """
        # 1. Normalize to [0, 1]
        data = data.astype(float)
        # Handle NaN/Inf
        data = np.nan_to_num(data)
        
        d_min = np.min(data)
        d_max = np.max(data)
        
        if d_max > d_min:
            data = (data - d_min) / (d_max - d_min)
        else:
            return np.zeros_like(data)
            
        # 2. Statistics
        median = np.median(data)
        mad = np.median(np.abs(data - median))
        
        # 3. Shadows / Highlights
        c0 = median + shadows_clip * mad
        c0 = max(0.0, c0) # Clip to 0
        c1 = 1.0 # Default highlight clip
        
        # Clip data
        data = np.clip(data, c0, c1)
        
        # Re-normalize to [0, 1] after clipping
        if c1 > c0:
            data = (data - c0) / (c1 - c0)
        else:
            pass # Should be flat
            
        # 4. Midtones Transfer Function (MTF)
        # Find 'm' such that MTF(m, median_new) = target_bg
        median_new = np.median(data)
        
        x = median_new
        y = target_bg
        
        m = 0.5
        if x > 0 and x < 1 and x != y:
            denom = x + y - 2 * x * y
            if denom != 0:
                m = (x * (1 - y)) / denom
        
        logger.debug(f"STF Stats: median={median:.6f}, mad={mad:.6f}, c0={c0:.6f}, median_new={median_new:.6f}, m={m:.6f}")

        # Apply MTF
        # (m - 1) * x / ((2 * m - 1) * x - m)
        if m != 0.5:
             # Vectorized application
             # Avoid division by zero
             term1 = (m - 1) * data
             term2 = (2 * m - 1) * data - m
             
             # Safe arithmetic
             with np.errstate(divide='ignore', invalid='ignore'):
                 data = term1 / term2
             
        data = np.clip(data, 0, 1)
        return (data * 255).astype(np.uint8)

    @staticmethod
    def load_source_image(source_path: str, is_subframe: bool = True, apply_stf: bool = False) -> Image.Image:
        """
        Loads a source image (FITS, RAW, Standard) and returns a PIL Image object (RGB).
        
        Args:
            source_path: Path to file
            is_subframe: If True, uses linear extraction for RAWs suitable for processing.
            apply_stf: If True, applies PixInsight-style STF Auto Stretch.
                       If False, applies simple normalization or uses default gamma.
        """
        source = Path(source_path)
        if not source.exists():
            logger.error(f"Source file not found: {source_path}")
            return None
            
        img = None
        ext = source.suffix.lower()
        logger.debug(f"Loading image: {source_path} | Ext: '{ext}' | Subframe: {is_subframe} | STF: {apply_stf}")
        
        try:
            # --- 1. RAW files (.cr2, .nef, etc) ---
            if ext in ['.cr2', '.nef', '.arw', '.dng', '.raf', '.cr3'] and rawpy:
                try:
                    with rawpy.imread(source_path) as raw:
                        if is_subframe:
                            # Linear extraction
                            rgb = raw.postprocess(
                                gamma=(1, 1),
                                no_auto_bright=True,
                                output_bps=16,
                                use_camera_wb=True
                            )
                            if apply_stf:
                                rgb = ThumbnailGenerator.apply_stf_stretch(rgb)
                            else:
                                # Simple normalize for visualization if linear
                                # Or just simple scale to 8-bit?
                                # If we return raw linear 16-bit without STF, it will be very dark.
                                # Simple min-max scan to view it?
                                # User asked for "linear images".
                                # Let's do a simple 16->8 bit scaling or min/max normalize?
                                # Let's do Min-Max Standard implementation for consistency
                                # Standard Normalize (Linear Scale)
                                d_min = np.min(rgb)
                                d_max = np.max(rgb)
                                if d_max > d_min:
                                    rgb = (rgb - d_min) / (d_max - d_min)
                                else:
                                    rgb = rgb - d_min
                                rgb = np.clip(rgb, 0, 1) * 255
                                rgb = rgb.astype(np.uint8)
                                
                            img = Image.fromarray(rgb)
                        else:
                            # Standard processing (Gamma corrected)
                            rgb = raw.postprocess(use_camera_wb=True, bright=1.0)
                            img = Image.fromarray(rgb)
                            
                except Exception as e:
                    print(f"RawPy processing failed for {source_path}: {e}")
            
            # --- 2. FITS files ---
            elif ext in ['.fits', '.fit']:
                if not fits:
                    logger.error("Astropy not installed but .fits file requested")
                    return None
                   
                try:
                    with fits.open(source_path) as hdul:
                        data = None
                        for hdu in hdul:
                            if hdu.data is not None and len(hdu.data.shape) >= 2:
                                data = hdu.data
                                break
                        
                        if data is not None:
                            if len(data.shape) == 3:
                                data = data[0] # Take first channel
                                
                            data = data.astype(float)
                            data = np.nan_to_num(data)
                            
                            if apply_stf:
                                data = ThumbnailGenerator.apply_stf_stretch(data)
                            else:
                                # Standard Normalize (Linear Stretch)
                                # Standard Normalize (Linear Scale)
                                d_min = np.min(data)
                                d_max = np.max(data)
                                if d_max > d_min:
                                    data = (data - d_min) / (d_max - d_min)
                                else:
                                    data = data - d_min
                                data = np.clip(data, 0, 1) * 255
                                data = data.astype(np.uint8)
                            
                            img = Image.fromarray(data)
                        else:
                             return None

                except Exception as e:
                    logger.exception(f"FITS processing failed for {source_path}")
                    return None
            
            # --- 3. XISF files ---
            elif ext == '.xisf':
                if not xisf:
                    return None
                
                try:
                    data = xisf.XISF.read(source_path)
                    if data is not None:
                        # Shape handling
                        if len(data.shape) == 3:
                            if data.shape[2] in [3, 4]: pass
                            elif data.shape[0] in [3, 4]: data = np.transpose(data, (1, 2, 0))
                            else:
                                if data.shape[2] == 1: data = np.squeeze(data, axis=2)
                                elif data.shape[0] == 1: data = np.squeeze(data, axis=0)
                                else: data = data[0]

                        data = data.astype(float)
                        data = np.nan_to_num(data)
                        
                        if apply_stf:
                            data = ThumbnailGenerator.apply_stf_stretch(data)
                        else:
                            # Standard Normalize (Linear Scale)
                            d_min = np.min(data)
                            d_max = np.max(data)
                            if d_max > d_min: data = (data - d_min) / (d_max - d_min)
                            else: data = data - d_min
                            data = np.clip(data, 0, 1) * 255
                            data = data.astype(np.uint8)
                            
                        img = Image.fromarray(data)
                    else:
                        return None
                except Exception as e:
                    logger.exception(f"XISF processing failed for {source_path}")
                    return None
            
            # --- 4. Standard Images (JPG, TIFF, etc) ---
            if img is None and ext not in ['.fits', '.fit', '.xisf', '.cr2', '.nef', '.arw', '.dng', '.raf', '.cr3']:
                try:
                    img = Image.open(source_path)
                    img.load()
                    # Standard images don't usually use STF, but if requested:
                    # (Usually only relevant for 16-bit TIFFs which we act on below in try-except fallback or high-bit check)
                except Exception as e:
                    # Fallback for TIFFs
                    if ext in ['.tif', '.tiff'] and tifffile:
                        try:
                            data = tifffile.imread(source_path)
                            if data is not None:
                                data = data.astype(float)
                                data = np.nan_to_num(data)
                                
                                if apply_stf:
                                    data = ThumbnailGenerator.apply_stf_stretch(data)
                                else:
                                    d_min = np.min(data)
                                    d_max = np.max(data)
                                    if d_max > d_min:
                                        data = (data - d_min) / (d_max - d_min)
                                    data = (np.clip(data, 0, 1) * 255).astype(np.uint8)
                                    
                                img = Image.fromarray(data)
                        except Exception as te:
                            img = None
                    else:
                        img = None
            
            if img:
                # Post-processing for loaded images (STF and high-bit normalization)
                # Specialized loaders (Fits, Xisf, Raw) already handle STF internally.
                SpecialExtensions = ['.fits', '.fit', '.xisf', '.cr2', '.nef', '.arw', '.dng', '.raf', '.cr3']
                was_handled = ext in SpecialExtensions
                
                # Check for high bit depth modes
                high_bit_modes = ('I', 'I;16', 'I;16L', 'I;16B', 'I;16S', 'F', 'I;32', 'I;32L', 'I;32B')
                is_high_bit = img.mode in high_bit_modes
                
                logger.debug(f"Post-processing: handled={was_handled}, high_bit={is_high_bit}, mode={img.mode}")

                # ONLY enter this block if it wasn't already handled by a specialized loader
                # OR if it's a high-bit image that Pillow didn't handle well but we can.
                if (apply_stf or is_high_bit) and not was_handled:
                    arr = np.array(img).astype(float)
                    arr = np.nan_to_num(arr)
                    
                    if apply_stf:
                        # STF Stretch (Auto-Stretch)
                        processed_arr = ThumbnailGenerator.apply_stf_stretch(arr)
                    else:
                        # Simple Linear Normalization for high-bit files
                        d_min = np.min(arr)
                        d_max = np.max(arr)
                        if d_max > d_min: 
                            arr = (arr - d_min) / (d_max - d_min)
                        else: 
                            arr = arr - d_min
                        processed_arr = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
                    
                    # Create new image from processed array
                    if len(processed_arr.shape) == 3:
                        img = Image.fromarray(processed_arr, mode='RGB')
                    else:
                        img = Image.fromarray(processed_arr, mode='L')

                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
            return img
            
        except Exception as e:
            logger.exception(f"Failed to load source image {source_path}")
            return None

    @staticmethod
    def generate(source_path: str, output_dir: str, max_size=(800, 800), is_subframe: bool = True, apply_stf: bool = False, overwrite: bool = False) -> str:
        """
        Generates a JPEG thumbnail for the given image file.
        
        Args:
            source_path: Absolute path to source file
            output_dir: Directory to save thumbnail
            max_size: Tuple of (width, height)
            is_subframe: Whether to interpret as subframe (linear extraction for RAWs)
            apply_stf: Whether to apply STF Auto Stretch
            overwrite: Whether to overwrite existing thumbnail
        """
        import hashlib
        source = Path(source_path)
        os.makedirs(output_dir, exist_ok=True)
        
        path_hash = hashlib.md5(str(source).encode('utf-8')).hexdigest()[:8]
        thumb_filename = f"{source.stem}_{path_hash}_thumb.jpg"
        thumb_path = os.path.join(output_dir, thumb_filename)
        
        # Check if thumbnail already exists to avoid redundant processing
        if os.path.exists(thumb_path) and not overwrite:
            logger.debug(f"Thumbnail already exists: {thumb_path}")
            return thumb_path
            
        try:
            img = ThumbnailGenerator.load_source_image(source_path, is_subframe=is_subframe, apply_stf=apply_stf)
            
            if img:
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                # Strip metadata to avoid save errors (e.g. malformed XMP)
                img.info = {}
                img.save(thumb_path, "JPEG", quality=85)
                return thumb_path
                
        except Exception as e:
            logger.exception(f"Failed to generate thumbnail for {source_path}: {e}")
            return None
            
        return None

