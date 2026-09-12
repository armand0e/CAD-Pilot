"""Hand-authored assistant demonstrations, NOT human-expert or on-policy data.

No model responses, measurements, screenshots or tool successes are fabricated here.
The replay runner captures those from real execution. All cases belong to training
families, are quarantined, and must not be mixed into cad-trajectory visual SFT.
"""
import copy


def feature(name, kind, dimensions=(), position=('0', '0', '0'), inputs=(), rotation=(0, 0, 0)):
    return {'id': name, 'kind': kind, 'dimensions': list(dimensions), 'position': list(position),
            'rotation': list(rotation), 'inputs': list(inputs)}


def recipe(name, parameters, features, result):
    return {'name': name, 'parameters': [{'name': k, 'value': v} for k, v in parameters.items()],
            'features': features, 'result': result}


PLATE = recipe('Four-hole mounting plate', {'length': 60, 'width': 40, 'thickness': 8, 'hole_diameter': 5, 'offset': 8}, [
    feature('Stock', 'box', ('length', 'width', 'thickness')),
    feature('NearLeft', 'cylinder', ('hole_diameter/2', 'thickness+2'), ('offset', 'offset', '-1')),
    feature('FarLeft', 'cylinder', ('hole_diameter/2', 'thickness+2'), ('offset', 'width-offset', '-1')),
    feature('NearRight', 'cylinder', ('hole_diameter/2', 'thickness+2'), ('length-offset', 'offset', '-1')),
    feature('FarRight', 'cylinder', ('hole_diameter/2', 'thickness+2'), ('length-offset', 'width-offset', '-1')),
    feature('Plate', 'difference', inputs=('Stock', 'NearLeft', 'FarLeft', 'NearRight', 'FarRight')),
], 'Plate')

BLANK = recipe('Blank mounting plate', {'length': 60, 'width': 40, 'thickness': 8}, [
    feature('Stock', 'box', ('length', 'width', 'thickness')),
], 'Stock')

SPACER = recipe('Annular spacer', {'outer_diameter': 20, 'bore_diameter': 10, 'height': 15}, [
    feature('Stock', 'cylinder', ('outer_diameter/2', 'height')),
    feature('Bore', 'cylinder', ('bore_diameter/2', 'height+2'), ('0', '0', '-1')),
    feature('Spacer', 'difference', inputs=('Stock', 'Bore')),
], 'Spacer')

# A generic electronics enclosure with specified interfaces. NOT a Raspberry Pi
# enclosure: no device fit, fasteners, thermal or print-material certification.
ENCLOSURE = recipe('Vented enclosure with removable lid', {
    'length': 90, 'width': 60, 'height': 24, 'wall': 2, 'floor': 2,
    'corner_radius': 6, 'lid_thickness': 2, 'lip_height': 3,
    'clearance': .3, 'layout_gap': 10, 'port_width': 16, 'port_height': 8,
    'slot_width': 3, 'slot_length': 24,
}, [
    feature('Outer', 'rounded_box', ('length', 'width', 'height', 'corner_radius')),
    feature('Cavity', 'rounded_box', ('length-2*wall', 'width-2*wall', 'height', 'corner_radius-wall'), ('wall', 'wall', 'floor')),
    feature('Port', 'box', ('port_width', 'wall+2', 'port_height'), ('(length-port_width)/2', '-1', 'floor+4')),
    feature('Base', 'difference', inputs=('Outer', 'Cavity', 'Port')),
    feature('LidPlate', 'rounded_box', ('length', 'width', 'lid_thickness', 'corner_radius'), ('length+layout_gap', '0', '0')),
    feature('LipOuter', 'rounded_box', ('length-2*(wall+clearance)', 'width-2*(wall+clearance)', 'lip_height+lid_thickness', 'corner_radius-wall-clearance'), ('length+layout_gap+wall+clearance', 'wall+clearance', '0')),
    feature('LipInner', 'rounded_box', ('length-2*(2*wall+clearance)', 'width-2*(2*wall+clearance)', 'lip_height+lid_thickness+2', 'corner_radius-2*wall-clearance'), ('length+layout_gap+2*wall+clearance', '2*wall+clearance', '-1')),
    feature('LipRing', 'difference', inputs=('LipOuter', 'LipInner')),
    feature('LidStock', 'union', inputs=('LidPlate', 'LipRing')),
    feature('VentOne', 'box', ('slot_width', 'slot_length', 'lid_thickness+2'), ('length+layout_gap+length/2-15', '(width-slot_length)/2', '-1')),
    feature('VentTwo', 'box', ('slot_width', 'slot_length', 'lid_thickness+2'), ('length+layout_gap+length/2-5', '(width-slot_length)/2', '-1')),
    feature('VentThree', 'box', ('slot_width', 'slot_length', 'lid_thickness+2'), ('length+layout_gap+length/2+5', '(width-slot_length)/2', '-1')),
    feature('VentFour', 'box', ('slot_width', 'slot_length', 'lid_thickness+2'), ('length+layout_gap+length/2+15', '(width-slot_length)/2', '-1')),
    feature('Lid', 'difference', inputs=('LidStock', 'VentOne', 'VentTwo', 'VentThree', 'VentFour')),
    feature('PrintLayout', 'parts', inputs=('Base', 'Lid')),
], 'PrintLayout')

ROUNDED = recipe('Rounded plate', {'length': 60, 'width': 40, 'thickness': 8, 'corner_radius': 4}, [
    feature('Rounded', 'rounded_box', ('length', 'width', 'thickness', 'corner_radius')),
], 'Rounded')


def step(user, assistant, design, oracle, expected_error=None):
    return {'user': user, 'assistant': assistant, 'tool': 'native_model', 'design': copy.deepcopy(design),
            'oracle': oracle, 'expected_error': expected_error}


def episodes():
    longer = copy.deepcopy(PLATE)
    longer['parameters'][0]['value'] = 80
    longer['parameters'][3]['value'] = 6
    broken = copy.deepcopy(PLATE)
    broken['features'][1]['position'][0] = 'length+10'
    return [
        {'id': 'plate-create-and-revise', 'family': 'four-hole-plate', 'steps': [
            step('Create a 60 × 40 × 8 mm plate with four Ø5 through holes, each center 8 mm from adjacent edges.',
                 'I’ll drive the hole centers from the edge offsets so resizing preserves the mounting layout.', PLATE, 'plate'),
            step('Make it 80 mm long with Ø6 holes. Keep the other dimensions and offsets.',
                 'I’ll change only length and hole diameter, retaining the four offset relationships.', longer, 'plate-resized')]},
        {'id': 'spacer', 'family': 'annular-spacer', 'steps': [
            step('Make a 20 mm OD, 10 mm ID spacer, 15 mm high.',
                 'I’ll subtract a full-length bore and keep both diameters independently editable.', SPACER, 'spacer')]},
        {'id': 'rounded-plate', 'family': 'rounded-body', 'steps': [
            step('Make a 60 × 40 × 8 mm plate with R4 vertical corners. Keep the top and bottom edges sharp.',
                 'I’ll round only the four vertical corners; the radius remains an editable dimension.', ROUNDED, 'rounded')]},
        {'id': 'vented-enclosure', 'family': 'generic-enclosure', 'steps': [
            step('Create a generic 90 × 60 × 24 mm enclosure with R6 vertical corners, 2 mm walls and floor. '
                 'Include a 16 × 8 mm front opening centered on X, bottom at Z6; a separate 2 mm lid with '
                 'a 3 mm locating lip, 2 mm lip walls and 0.3 mm clearance per side; and four 3 × 24 mm lid vents '
                 'at local X30,40,50,60 and Y18. Put the inverted lid 10 mm to the right of the base. No fasteners needed.',
                 'I’ll build the hollow base and separate vented lid, with a clearance-controlled locating lip. '
                 'This is a generic enclosure, not a verified fit for a named device. The print layout is not an assembly simulation.',
                 ENCLOSURE, 'enclosure')]},
        {'id': 'missed-cutter-recovery', 'family': 'four-hole-plate', 'steps': [
            step('Create a 60 × 40 × 8 mm blank plate.',
                 'I’ll save the blank stock before adding any features.', BLANK, 'blank-plate'),
            step('Add four Ø5 through holes, each centered 8 mm from its adjacent edges.',
                 'I’ll build the plate and check that all four cutters remove material.', broken, None, 'removed no material'),
            step(None, 'The tool reports that NearLeft misses the stock. Its X position was length+10; '
                 'I’ll correct it to offset, keeping the required hole and all other dimensions.', PLATE, 'plate')]},
    ]
