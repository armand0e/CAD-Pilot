# Texturing and materials (make the surface believable)

Form gives a model its shape; materials give it its identity. A grey model reads as a
prototype; the right materials make it read as skin, brass, denim, or a glowing core. In
the Cycles render every material shows, so treat this as a real stage, not an afterthought.

## PBR: the five inputs that matter

Blender's Principled BSDF is physically based. Set these and almost anything reads right:

- Base Color - the colour (RGBA 0..1). For realism keep it in a believable range (skin
  is not pure white; "black" plastic is ~0.05, not 0).
- Metallic - 1.0 for bare metal, 0.0 for everything else. There is no in-between in
  reality (a painted metal is 0 with the paint's colour).
- Roughness - the single most expressive control. 0 is a mirror, 1 is chalk. Polished
  metal ~0.1, plastic ~0.4, skin ~0.5, rubber/cloth ~0.8. Vary it and the surface lives.
- Normal - fine surface relief without geometry, driven by a Bump or Normal Map node.
- Emission Color / Emission Strength - self-illumination for glows, screens, lava, eyes.

```python
mat = bpy.data.materials.new('brass'); mat.use_nodes = True
b = mat.node_tree.nodes['Principled BSDF']
b.inputs['Base Color'].default_value = (0.83, 0.69, 0.32, 1)
b.inputs['Metallic'].default_value = 1.0
b.inputs['Roughness'].default_value = 0.28
obj.data.materials.append(mat)
# a glow: b.inputs['Emission Color'].default_value=(0.1,0.6,1,1); b.inputs['Emission Strength'].default_value=3
# glass: b.inputs['Transmission Weight'].default_value=1.0; b.inputs['Roughness'].default_value=0.0
```

## Material zones - so the parts read apart

A character needs skin, hair, eyes and clothing as SEPARATE materials, or it reads as one
lump. Give the object several material slots and assign each polygon by region (position):

```python
for name in ('skin', 'hair', 'eye', 'dress'):
    m = bpy.data.materials.new(name); m.use_nodes = True; obj.data.materials.append(m)
for poly in obj.data.polygons:
    z = obj.data.vertices[poly.vertices[0]].co.z
    poly.material_index = 0 if z < 60 else 1     # ...refine by region: eyes by proximity, etc.
```

Colour the zones with contrast that suits the subject and the style.

## Procedural texture, or an image

- Procedural (no files): wire Noise/Voronoi/Wave/Musgrave into Base Color (through a
  Color Ramp) and into a Bump -> Normal for relief - rock, skin pores, brushed metal,
  wood. Scale and detail control the look. See blender-modeling.md for the node wiring.
- Image texture: unwrap UVs, then feed an image into Base Color. Unwrap with Smart UV
  Project; load an attached/research image from /work/images (blender-modeling.md).

```python
bpy.context.view_layer.objects.active = obj; bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT'); bpy.ops.uv.smart_project(angle_limit=1.15)
bpy.ops.object.mode_set(mode='OBJECT')
```

## Style: realistic vs stylised

- Realistic: PBR values in real ranges, subtle roughness variation, a normal texture for
  micro-detail, restrained colour.
- Stylised / anime / toon: flat, saturated, distinct colours per zone; low roughness
  variation; strong readable regions over surface detail. For a stylised character, clean
  flat colours that read apart beat busy textures.

## Checklist

- Metallic is 0 or 1; roughness is set per material and varied across the model.
- Colours are in believable ranges (no pure white/black bases for realism).
- Distinct regions (skin/hair/eyes/clothing, or panels/trim) have distinct materials.
- Emission used for anything that should glow; glass uses transmission.
- Procedural or image texture adds surface character where it helps; UVs unwrapped if
  using an image. The render reads as the material you intended.
