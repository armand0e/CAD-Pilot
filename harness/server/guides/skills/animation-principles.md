# Animation principles (make motion read, not just move)

Moving an object between two frames is not animation - it is a slide. What makes motion
read as alive is timing and the classic principles. A short, well-timed motion beats a
long mechanical one. The build records up to ~120 frames, so keep it tight and looping.

## The principles that matter most here

- Timing and spacing: how long a motion takes and how the in-between frames are spaced.
  Even spacing looks robotic; ease-in and ease-out (slow at the ends, fast in the middle)
  looks natural. This alone fixes most bad motion.
- Anticipation: a small opposite move before the main action (crouch before a jump, wind
  up before a wave). It tells the eye what is coming.
- Follow-through and overlap: parts keep moving and settle after the main mass stops
  (hair, a cape, an arm overshoots then eases back). Nothing stops dead.
- Arcs: natural motion travels in curves, not straight lines. A waving hand arcs.
- Staging and hold: present the motion clearly to camera and HOLD the readable pose a
  beat so the viewer registers it.

## Easing - the single biggest upgrade

Keyframe interpolation is BEZIER by default (already eased), but make it deliberate:

```python
obj.location.z = 30; obj.keyframe_insert('location', frame=1)
obj.location.z = 5;  obj.keyframe_insert('location', frame=12)   # ease down
obj.location.z = 30; obj.keyframe_insert('location', frame=24)   # ease up (a bounce)
for fc in obj.animation_data.action.fcurves:
    for kp in fc.keyframe_points:
        kp.interpolation = 'BEZIER'; kp.handle_left_type = kp.handle_right_type = 'AUTO_CLAMPED'
```

For a mechanical, constant-speed motion (a turning gear) use `kp.interpolation = 'LINEAR'`
instead - the exception that proves the rule.

## Practical motions

- Turntable: orbit the CAMERA around the still subject over the frame range (rotate an
  empty the camera is parented to, or keyframe the camera location on a circle). Shows the
  whole model; the subject stays put and watertight.
- Idle: a slow up-down sway plus a tiny rotation, eased, looping (frame 1 and last match).
- Wave / gesture: rig an arm (blender-modeling.md) and keyframe the pose bones with
  anticipation and follow-through.
- Blink / morph: shape keys animate between mesh shapes (create a Basis and a target key,
  keyframe key_blocks[...].value 0 -> 1 -> 0).

## Loop it

If it repeats, make frame 1 and the last frame identical so the MP4 loops seamlessly. Hold
the key pose for a few frames at the ends.

## Checklist

- Motion eases in and out (BEZIER, not linear) unless it is deliberately mechanical.
- The main action has anticipation before and settle/follow-through after.
- Motion follows arcs; the pose is staged clearly to camera and held a beat.
- It is short and, if repeating, loops seamlessly (first and last frame match).
- The frame-1 pose is the one you want as the still and the printable mesh.
