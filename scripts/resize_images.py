import os
import sys
from PIL import Image

def resize_images(directory):
    print(f"Scanning {directory}...")
    if not os.path.exists(directory):
        print(f"Directory {directory} does not exist.")
        return

    count = 0
    saved_space = 0

    for filename in os.listdir(directory):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
            filepath = os.path.join(directory, filename)
            try:
                original_size = os.path.getsize(filepath)
                
                # Skip small files (less than 500KB) unless dimensions are huge
                if original_size < 500 * 1024:
                    with Image.open(filepath) as img:
                        if img.width <= 1024 and img.height <= 1024:
                            continue

                with Image.open(filepath) as img:
                    # Convert to RGB if necessary (e.g. RGBA pngs)
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    
                    # Resize if larger than 1024x1024
                    if img.width > 1024 or img.height > 1024:
                        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    
                    # Save back to same file with optimization
                    # We force JPEG for compression efficiency unless it was strictly required to be PNG
                    # But to be safe, we keep extension logic or just overwrite
                    # If we overwrite, we should respect the format, but PIL determines format from extension usually.
                    
                    img.save(filepath, quality=85, optimize=True)
                    
                    new_size = os.path.getsize(filepath)
                    saved = original_size - new_size
                    saved_space += saved
                    count += 1
                    print(f"Resized {filename}: {original_size/1024:.1f}KB -> {new_size/1024:.1f}KB")
            except Exception as e:
                print(f"Failed to process {filename}: {e}")

    print(f"Done. Resized {count} images. Saved {saved_space/1024/1024:.2f}MB.")

if __name__ == "__main__":
    # Default to production path if no arg provided
    target_dir = sys.argv[1] if len(sys.argv) > 1 else '/images'
    
    # Fallback for local dev if default path doesn't exist
    if target_dir == '/images' and not os.path.exists(target_dir):
        if os.path.exists('static/uploads'):
             target_dir = 'static/uploads'
        elif os.path.exists('/tmp/images'):
             target_dir = '/tmp/images'
        
    resize_images(target_dir)
