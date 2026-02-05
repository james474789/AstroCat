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
        
        # Apply MTF
        # (m - 1) * x / ((2 * m - 1) * x - m)
        if m != 0.5:
             # Vectorized application
             # Avoid division by zero
             term1 = (m - 1) * data
             term2 = (2 * m - 1) * data - m
             # Handle term2 == 0 cases by replacing with small epsilon or masking? 
             # MTF is monotonic, singularity at m/(2m-1) which is outside [0,1] for m in [0,1]?
             # if m=0.5, term2 = 0*x - 0.5 = -0.5. No div zero.
             # if m=0, term2 = -x. Div zero if x=0. But x in [0,1].
             # if m=1, term2 = x - 1. Div zero if x=1.
             
             # Safe arithmetic
             with np.errstate(divide='ignore', invalid='ignore'):
                 data = term1 / term2
             
        data = np.clip(data, 0, 1)
        return (data * 255).astype(np.uint8)

    @staticmethod
    def load_source_image(source_path: str, is_subframe: bool = True) -> Image.Image:
        """
        Loads a source image (FITS, RAW, Standard) and returns a PIL Image object (RGB).
        
        Args:
            source_path: Path to file
            is_subframe: If True, applies STF Auto Stretch for linear data. 
                         If False, logic assumes pre-stretched/master and attempts simple loading.
        """
        source = Path(source_path)
        if not source.exists():
            logger.error(f"Source file not found: {source_path}")
            return None
            
        img = None
        ext = source.suffix.lower()
        logger.debug(f"Loading image: {source_path} | Ext: '{ext}' | Subframe: {is_subframe}")
        
        try:
            # --- 1. RAW files (.cr2, .nef, etc) ---
            if ext in ['.cr2', '.nef', '.arw', '.dng', '.raf', '.cr3'] and rawpy:
                try:
                    with rawpy.imread(source_path) as raw:
                        if is_subframe:
                            # Linear extraction for STF
                            rgb = raw.postprocess(
                                gamma=(1, 1),
                                no_auto_bright=True,
                                output_bps=16,
                                use_camera_wb=True # Use WB if available, but keep linear
                            )
                            # Apply STF
                            rgb_stf = ThumbnailGenerator.apply_stf_stretch(rgb)
                            img = Image.fromarray(rgb_stf)
                        else:
                            # Legacy/Default behavior for non-subframes (e.g. DSLR raw that shouldn't be stretched?)
                            # Usually RAWs are subframes, but if instructed otherwise:
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
                            
                            if is_subframe:
                                data = ThumbnailGenerator.apply_stf_stretch(data)
                            else:
                                # Simple Linear Stretch (Percentile) -> Only if not subframe?
                                # User said "for all other image types no stretch should be applied".
                                # But FITS data is often float/int32/uint16. 
                                # If we don't normalize, it might display black.
                                # Assuming "no stretch" means "Standard Normalization" without "Auto Stretch curve".
                                # Let's keep the existing simple normalization logic for non-subframes 
                                # to ensure visibility, OR respect "no stretch" strictly.
                                # "No stretch" strictly implies linear scaling min-max?
                                # I'll use simple min-max scaling to 0-255 if it's not a subframe
                                # to ensure it's viewable.
                                low, high = np.percentile(data, (1, 99))
                                if high > low:
                                    data = (data - low) / (high - low)
                                else:
                                    data = data - low
                                data = np.clip(data, 0, 1) * 255
                                data = data.astype(np.uint8)
                            
                            img = Image.fromarray(data)
                            img = ImageOps.flip(img)
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
                        # Shape handling... same as before
                        if len(data.shape) == 3:
                            if data.shape[2] in [3, 4]: pass
                            elif data.shape[0] in [3, 4]: data = np.transpose(data, (1, 2, 0))
                            else:
                                if data.shape[2] == 1: data = np.squeeze(data, axis=2)
                                elif data.shape[0] == 1: data = np.squeeze(data, axis=0)
                                else: data = data[0]

                        data = data.astype(float)
                        data = np.nan_to_num(data)
                        
                        if is_subframe:
                            data = ThumbnailGenerator.apply_stf_stretch(data)
                            img = Image.fromarray(data)
                        else:
                            # Standard Normalize
                            low, high = np.percentile(data, (1, 99))
                            if high > low: data = (data - low) / (high - low)
                            else: data = data - low
                            data = np.clip(data, 0, 1) * 255
                            data = data.astype(np.uint8)
                            img = Image.fromarray(data)
                            
                        # XISF flip
                        img = ImageOps.flip(img)
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
                    # Standard images usually don't need STF (already gamma corrected)
                except Exception as e:
                    # Fallback for TIFFs that Pillow might not handle (e.g. 32-bit float)
                    if ext in ['.tif', '.tiff'] and tifffile:
                        try:
                            logger.debug(f"Pillow failed for {ext}, trying tifffile...")
                            data = tifffile.imread(source_path)
                            if data is not None:
                                # Data scaling/handling
                                data = data.astype(float)
                                data = np.nan_to_num(data)
                                
                                # DeepSkyStacker TIFFs are often in [0, 1] for float32 or [0, 65535] for uint16
                                # If it's a subframe, we should use STF. 
                                # If not, we still need to normalize to uint8 for Image.fromarray
                                if is_subframe:
                                    data = ThumbnailGenerator.apply_stf_stretch(data)
                                else:
                                    # Simple normalize
                                    d_min = np.min(data)
                                    d_max = np.max(data)
                                    if d_max > d_min:
                                        data = (data - d_min) / (d_max - d_min)
                                    data = (np.clip(data, 0, 1) * 255).astype(np.uint8)
                                    
                                img = Image.fromarray(data)
                        except Exception as te:
                            logger.error(f"tifffile fallback failed for {source_path}: {te}")
                            img = None
                    else:
                        img = None
            
            # --- 5. Validating and ensuring RGB ---
            # If img is still None, try fallbacks (omitted for brevity, relying on specialized above)
            
            if img:
                # High bit depth standard images (TIFF 16-bit)
                high_bit_modes = ('I', 'I;16', 'I;16L', 'I;16B', 'I;16S', 'F', 'I;32', 'I;32L', 'I;32B')
                if img.mode in high_bit_modes:
                    arr = np.array(img).astype(float)
                    arr = np.nan_to_num(arr)
                    if is_subframe:
                        arr = ThumbnailGenerator.apply_stf_stretch(arr)
                        img = Image.fromarray(arr, mode='L') # Greyscale result
                    else:
                        # Simple normalize
                        low, high = np.percentile(arr, (1, 99))
                        if high > low: arr = (arr - low) / (high - low)
                        else: arr = arr - low
                        arr = np.clip(arr, 0, 1) * 255
                        img = Image.fromarray(arr.astype(np.uint8), mode='L')

                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
            return img
            
        except Exception as e:
            logger.exception(f"Failed to load source image {source_path}")
            return None

    @staticmethod
    def generate(source_path: str, output_dir: str, max_size=(800, 800), is_subframe: bool = True) -> str:
        """
        Generates a JPEG thumbnail for the given image file.
        
        Args:
            source_path: Absolute path to source file
            output_dir: Directory to save thumbnail
            max_size: Tuple of (width, height)
            is_subframe: Whether to apply STF Auto Stretch (default True for safety, caller should specify)
        """
        import hashlib
        source = Path(source_path)
        os.makedirs(output_dir, exist_ok=True)
        
        path_hash = hashlib.md5(str(source).encode('utf-8')).hexdigest()[:8]
        thumb_filename = f"{source.stem}_{path_hash}_thumb.jpg"
        thumb_path = os.path.join(output_dir, thumb_filename)
        
        try:
            img = ThumbnailGenerator.load_source_image(source_path, is_subframe=is_subframe)
            
            if img:
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                img.save(thumb_path, "JPEG", quality=85)
                return thumb_path
                
        except Exception as e:
            logger.exception(f"Failed to generate thumbnail for {source_path}: {e}")
            return None
            
        return None

