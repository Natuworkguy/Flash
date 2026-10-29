---
name: make-a-3d-model
description: Build a 3D object, scene or layout the user can turn and zoom
---
make_3d_model builds a .glb from parts and shows it in a 3D viewer.

1. Think of the real thing's size in metres and break it into simple parts: boxes, cylinders, spheres, cones and so on.
2. Y is up and the ground is y = 0. Every shape is centred on its position, so a part sitting on the ground has y equal to half its height.
3. Use many parts in true proportions, coloured like the real thing; give surfaces textures (wood, brick, grass) and text parts for any words on it.
4. Animate parts that move in reality (a fan, clock hands, wheels) with animate.
5. Read the result's overall size and look at its picture; fix what is off and call again with the whole list and the same path.

send_3d_model shows an existing .glb, .stl or .obj file.

## Examples

A table: a top and four legs resting on the ground.
```
make_3d_model {"path": "~/Downloads/table.glb", "title": "Table", "parts": [{"name": "top", "shape": "box", "size": [1.2, 0.04, 0.7], "position": [0, 0.74, 0], "color": "#8b5a2b", "texture": "wood"}, {"name": "leg front left", "shape": "box", "size": [0.05, 0.72, 0.05], "position": [-0.55, 0.36, 0.3], "color": "#6b4423"}, {"name": "leg front right", "shape": "box", "size": [0.05, 0.72, 0.05], "position": [0.55, 0.36, 0.3], "color": "#6b4423"}, {"name": "leg back left", "shape": "box", "size": [0.05, 0.72, 0.05], "position": [-0.55, 0.36, -0.3], "color": "#6b4423"}, {"name": "leg back right", "shape": "box", "size": [0.05, 0.72, 0.05], "position": [0.55, 0.36, -0.3], "color": "#6b4423"}]}
```

A moving part.
```
make_3d_model {"path": "~/Downloads/fan.glb", "parts": [{"name": "blades", "shape": "cylinder", "radius": 0.3, "height": 0.01, "position": [0, 1, 0], "rotation": [90, 0, 0], "animate": {"spin": {"axis": "z", "seconds": 0.5}}}]}
```
