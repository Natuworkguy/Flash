---
name: view-an-image
description: Look at a local image file (a screenshot, a frame, a chart) and say what is in it
---
- view_image takes an absolute path to a .png, .jpg, .jpeg, .gif, .webp or .bmp file of at most 20 MB and attaches it, so you see the picture with the tool result.
- Only files under the home folder, a temp folder (/tmp) or /Volumes. Hidden paths (any part starting with a dot, such as ~/.ssh) are refused.
- When the active model cannot see images, it says so and attaches nothing: tell the user to switch to a vision model instead of guessing.
- Describe only what you can actually see. Call it again for another look or another file.
- Sparks have it too; sub-agents do not.
- For frames of a YouTube video, watch_youtube attaches them itself; view_image is for frames already saved to disk.

Example:
view_image {"path": "/tmp/ytw/sheet1.jpg"}
