---
name: work-with-images
description: Look at, edit, convert or create images, and show them
---
- view_image looks at an image on disk; it is the only way you can see one. It takes an absolute path under home, a temp folder or /Volumes, never a hidden one (see view-an-image). Check every image you make before sending it.
- send_image shows the user a finished image.
- screenshot renders a page or URL and shows you the picture.

New pictures: image create with a full prompt (the subject, what they do, the setting, the light, the style or camera and lens, the mood), a size for where it is going (square for a feed, story for 9:16, wide or banner for a header), count 2 to 4 when the user should pick. Changes to a picture: image edit with its path and what to change ("make it night", "the same woman, now in a red coat, waving"); to keep a character the same across pictures, edit from the first picture of them every time rather than describing them again. image remove_background cuts the subject out; image upscale makes it bigger. Look at every result with view_image, and redo it if hands, faces, text or edges went wrong. With no image model in Ollama, the tool says which to pull: tell the user, in one line.

Other edits by script: write a short script with shell. Python with Pillow (pip install pillow) for resizing, cropping, converting, compositing and drawing text; ImageMagick (magick) where installed. For graphics, an SVG or an HTML page rendered with screenshot often looks better than drawing pixels.

Charts as images: matplotlib (pip install matplotlib), saved as PNG at dpi=200, then view_image to check labels are readable.

Pitfalls: keep the original, write to a new file; mind the colour mode (RGBA to JPEG needs converting to RGB).

## Examples

```
image {"action": "create", "prompt": "A small orange cat asleep on a stack of library books, late afternoon sun through tall windows, warm film photo, 50mm lens, shallow depth of field", "size": "landscape", "count": 2}
```

```
image {"action": "edit", "path": "~/.flash/images/cat.png", "prompt": "the same cat and books at night, lit by a desk lamp"}
```

```
shell {"command": "python -c \"from PIL import Image; im = Image.open('photo.png').convert('RGB'); im.thumbnail((1200, 1200)); im.save('photo-small.jpg', quality=85)\""}
```

```
view_image {"path": "photo-small.jpg"}
```

```
send_image {"path": "photo-small.jpg", "caption": "Resized to 1200 px"}
```
