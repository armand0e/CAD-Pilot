# Lighting, camera and rendering (presentation is half the result)

The same model looks like a prototype under flat light and like a product under good
light. The build renders Cycles for you and, if you add no camera or lights, drops in a
default studio - but for anything you care about, set up the shot yourself. The harness
uses your camera and lights when you provide them.

## Three-point lighting - the foundation

- Key: the main light, brightest, off to one side and above (~45 deg). Sets the form.
- Fill: softer, opposite the key, ~1/4 the key's power. Lifts the shadows so they read.
- Rim (back light): behind the subject, catches the edges and separates it from the
  background. This is what makes a render "pop".
Use AREA lights (soft shadows) sized to the subject; a large soft key flatters organic
form, a smaller harder key defines hard-surface.

```python
sc = bpy.context.scene
def light(name, energy, size, loc):
    d = bpy.data.lights.new(name, 'AREA'); d.energy = energy; d.size = size
    o = bpy.data.objects.new(name, d); sc.collection.objects.link(o); o.location = loc
    o.rotation_euler = (o.location.normalized() if False else (0.0, 0.0, 0.0))  # aim by hand below
    return o
key  = light('Key',  900, 30, ( 45, -45, 65))
fill = light('Fill', 220, 55, (-55, -30, 30))
rim  = light('Rim',  700, 20, (  0,  45, 55))
# point each light at the subject centre:
import mathutils
for L in (key, fill, rim):
    L.rotation_euler = (mathutils.Vector((0,0,40)) - L.location).to_track_quat('-Z','Y').to_euler()
```

## Environment and mood

Set the world colour and strength for ambient light and background tone. A dim cool world
with bright key gives drama; an even brighter world gives a clean product look. Warm key
plus cool fill reads natural.

```python
if sc.world is None: sc.world = bpy.data.worlds.new('W')
sc.world.use_nodes = True
bg = sc.world.node_tree.nodes['Background']
bg.inputs['Color'].default_value = (0.05, 0.06, 0.08, 1); bg.inputs['Strength'].default_value = 0.3
sc.view_settings.view_transform = 'AgX'   # filmic tone mapping - realistic highlights, no blowout
```

## Camera - lens, angle, focus

- Angle: a three-quarter view (front + one side + slightly above) reads a subject best;
  a straight front works for symmetry, a low angle makes something heroic/imposing.
- Lens: 50-85 mm for a natural look; wide (24-35) exaggerates and distorts (avoid for
  portraits); long (100+) flattens. Frame so the subject fills the frame with a little air.
- Depth of field: enable dof and set a low f-stop to throw the background soft and hold
  focus on the subject - instantly more photographic.

```python
cd = bpy.data.cameras.new('Cam'); cd.lens = 80; cd.dof.use_dof = True; cd.dof.aperture_fstop = 2.8
cam = bpy.data.objects.new('Cam', cd); sc.collection.objects.link(cam); sc.camera = cam
cam.location = (55, -110, 55)
cam.rotation_euler = (mathutils.Vector((0,0,40)) - cam.location).to_track_quat('-Z','Y').to_euler()
```

## Composition

- Fill the frame; leave a little breathing room, more in the direction the subject faces.
- Put the focal feature (the face) near a thirds line, not dead centre, unless symmetry is
  the point.
- A ground plane with contact shadow grounds the object; a gradient or dim world keeps
  focus on it.

## Also set the saved views

Beyond the beauty render, set views = {...} (blender-modeling.md) to the angles that show
the model's features and expose its flaws - a straight-on face, a profile, a back. The
review reads these, so aim them honestly.

## Checklist

- Key/fill/rim set, AREA lights sized to the subject, each aimed at it.
- World tone and strength chosen for the intended mood; AgX view transform on.
- Camera at a flattering angle, 50-85 mm, framed with a little air, DoF if it suits.
- A focal feature placed with composition in mind; ground/contact shadow present.
- Saved views aimed at the features and the weak spots.
