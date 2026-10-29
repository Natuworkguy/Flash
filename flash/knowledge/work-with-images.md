---
name: work-with-images
description: Look at, edit, convert or create images, and show them
---
- view_image looks at an image on disk; it is the only way you can see one. Check every image you make before sending it.
- send_image shows the user a finished image.
- screenshot renders a page or URL and shows you the picture.

Making and editing images: write a short script with shell. Python with Pillow (pip install pillow) for resizing, cropping, converting, compositing and drawing text; ImageMagick (magick) where installed. For graphics, an SVG or an HTML page rendered with screenshot often looks better than drawing pixels.

Charts as images: matplotlib (pip install matplotlib), saved as PNG at dpi=200, then view_image to check labels are readable.

Pitfalls: keep the original, write to a new file; mind the colour mode (RGBA to JPEG needs converting to RGB).

## Examples

```
shell {"command": "python -c \"from PIL import Image; im = Image.open('photo.png').convert('RGB'); im.thumbnail((1200, 1200)); im.save('photo-small.jpg', quality=85)\""}
```

```
view_image {"path": "photo-small.jpg"}
```

```
send_image {"path": "photo-small.jpg", "caption": "Resized to 1200 px"}
```
