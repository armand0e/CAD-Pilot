# Modelling from reference images and datasheets

When the part must match a real object - a phone, a bracket hole pattern, a connector,
a card - your model is only as good as your dimensions. Treat measuring as a first-class
step, and separate what you KNOW from what you GUESS.

## Get real numbers before modelling

Order of preference for a dimension:

1. A datasheet or spec. Use research to open the manufacturer page or PDF and read the
   printed value. Record it as a sourced requirement with the source id.
2. A dimensioned drawing the user gave. Read the printed dimensions; they are evidence.
3. A standard. Fan patterns, VESA, DIN rail, USB/HDMI cutouts, PCB mounting - look up
   the standard rather than eyeballing.
4. A scaled measurement off a photo. Only when nothing above exists, and mark it an
   assumption.

## Scaling from a photo

If you must measure from an image, scale from a KNOWN dimension in that same image:
find a feature whose real length you know (a printed "40 mm", a standard connector),
measure it in pixels, and use that ratio for other features. The images are readable
at full resolution under /work/images; view_image can crop to measure precisely.
Printed labels are evidence; pixel scaling is an estimate - record it as assumed with
a tolerance, never as a sourced measurement.

## Import exact geometry when you can

If a STEP/DXF/SVG/IGES file exists, import it instead of re-drawing:

- import_reference downloads a file into /work/references/.
- Part.Shape().read(path) opens STEP/IGES; Mesh.Mesh(path) opens STL.
- importDXF.insert(path, doc.Name) adds DXF entities - circle edges give EXACT hole
  centres and radii, which is the reliable way to copy a real hole pattern.
- importSVG.insert(path, doc.Name) adds SVG paths.

A DXF of a mounting face or a STEP of a mating part removes all the guesswork.

## Record evidence honestly in design-spec.json

- origin: user (they told you), sourced (from an opened page/datasheet), or assumed.
- Every sourced value cites the opened source id; every image observation cites the
  image id. Never promote an assumption to a measurement.
- Put uncertain dimensions in open_questions and parameterise them so they are one
  edit away when confirmed.

## Model the reference, but do not export it

Modelling the real object (the phone, the card) to check fit is good practice - keep
it as a reference solid and exclude it from parts (see connections-and-assembly.md).

## Checklist

- Every load-bearing dimension is sourced, standard, or an explicit parameterised assumption.
- Standards were looked up, not guessed.
- Exact files were imported where available (DXF circles for hole patterns).
- design-spec.json distinguishes sourced from assumed and lists open questions.
- Any modelled reference object is excluded from the exported parts.
