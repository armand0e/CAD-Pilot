# FDM-printed enclosures and cases (design notes)

Wall thickness: 1.6-2.4 mm for small boxes (2 mm is a safe default, 4 perimeters at 0.4 mm
nozzle); 2.5-3 mm for boxes over 150 mm. Floors and lids: 1.6-2 mm minimum.
Board clearance: 0.5-1 mm between the PCB edge and the inner wall; add 1-2 mm above the tallest
component. Standoffs: 3-6 mm tall under the board so solder joints and the SD card clear the floor.
Lid fit: a lip that enters the base needs 0.2-0.3 mm clearance per side; a friction fit uses
0.1-0.15 mm. Print-layout gap between separate parts: 0.5 mm or more.
Openings for connectors: connector body plus 0.5-1 mm on each side and above, 1 mm below;
USB-A opening about 14 x 7 mm, micro-USB 9 x 4.5 mm, USB-C 10 x 4 mm, HDMI 16 x 7 mm,
RJ45 17 x 14.5 mm, 3.5 mm jack 7 mm round, SD card 15 x 3 mm.
Screw bosses: outer diameter = screw diameter + 4 mm (M2.5 -> 6.5 mm), height to the board's
underside; hole = tap size (M2.5 -> 2.1 mm) for direct threading, 2.7-2.8 mm for a clearance hole,
3.2 mm for a heat-set insert (M2.5) / 4.0 mm (M3).
Ventilation: slots 2-3 mm wide with 2 mm ribs, or 3-4 mm round holes on a 6 mm grid; keep vents
away from the corners and at least one wall thickness from openings.
Overhangs: keep unsupported overhangs under 45 degrees; openings in vertical walls print fine
without support if their top edge is short (under 12 mm) or bridged.
Fillets: 1-3 mm on outer vertical corners look and print well; do not fillet the parting edge
between a lid and a base.
Tolerances: FDM holes come out 0.2-0.4 mm smaller than modelled; model clearance holes 0.3 mm
oversize. Layer height 0.2 mm; dimensions in Z snap to layers.
