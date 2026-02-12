
import sys
import logging
from app.extractors.factory import get_extractor

# Configure logging
logging.basicConfig(level=logging.INFO)

def main():
    if len(sys.argv) < 2:
        print("Usage: python debug_extractor.py <file_path>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    print(f"Testing extraction for: {file_path}")
    
    try:
        extractor = get_extractor(file_path)
        print(f"Extractor: {extractor.__class__.__name__}")
        
        metadata = extractor.extract()
        print("Metadata extracted successfully:")
        for k, v in metadata.items():
            if k == "raw_header":
                import json
                try:
                    json_str = json.dumps(v)
                    print(f"  {k}: <valid json, length {len(json_str)}>")
                except Exception as json_err:
                    print(f"  {k}: JSON ERROR: {json_err}")
            else:
                print(f"  {k}: {v}")
                
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
