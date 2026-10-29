"""Single source of truth for fixed domain vocabulary (spec §4.2, §5).

Generated documents must use the same component names as the graph.
"""

PLANTS = [
    {"id": "P1", "name": "Munich Assembly", "location": "Munich, DE"},
    {"id": "P2", "name": "Leipzig Machining", "location": "Leipzig, DE"},
]
# (line id, plant id) in global machine-distribution order
LINES = [("A1", "P1"), ("A2", "P1"), ("A3", "P1"), ("A4", "P1"),
         ("B1", "P2"), ("B2", "P2"), ("B3", "P2"), ("B4", "P2")]  # fmt: skip
# last machine of the first line feeds the first machine of the second line
CROSS_LINE_FEEDS = [("A2", "A3"), ("B2", "B3"), ("A4", "B1")]

MODELS = ["model1", "model2", "model3", "model4"]

COMPONENTS = {
    "comp1": "Spindle bearing",
    "comp2": "Hydraulic pump",
    "comp3": "Drive motor",
    "comp4": "Coolant valve",
}

SENSOR_UNITS = {"volt": "V", "rotate": "rpm", "pressure": "bar", "vibration": "mm/s"}
# sensors that also get low thresholds (spec §5.4)
SENSORS_WITH_LOW_THRESHOLDS = {"volt", "pressure"}

# The Kaggle dataset gives no semantics for error codes; these are synthetic.
ERROR_TYPES = {
    "error1": "Spindle vibration anomaly",
    "error2": "Hydraulic pressure irregularity",
    "error3": "Drive motor overload",
    "error4": "Coolant flow fault",
    "error5": "Controller communication fault",
}

TECHNICIAN_NAMES = [
    "Anna Keller", "Jonas Weber", "Mira Hoffmann", "Lukas Brandt", "Selin Yilmaz",
    "Tobias Richter", "Elena Vogel", "Marek Novak", "Hanna Berger", "Paul Lehmann",
]  # fmt: skip
SHIFTS = ["day", "evening", "night"]

SEED = 42

NODE_LABELS = [
    "Plant", "ProductionLine", "MachineModel", "Machine", "Component", "Sensor", "ErrorType",
    "ErrorEvent", "MaintenanceEvent", "Failure", "Alarm", "Technician", "Document", "Chunk",
]  # fmt: skip

RELATIONSHIP_TYPES = [
    "HAS_LINE", "HAS_MACHINE", "FEEDS_INTO", "IS_MODEL", "HAS_COMPONENT", "HAS_SENSOR",
    "OCCURRED_ON", "OF_TYPE", "PERFORMED_ON", "REPLACED", "PERFORMED_BY", "FAILED",
    "RESOLVED_BY", "RAISED_ON", "FROM_SENSOR", "SIMILAR_TO", "HAS_CHUNK", "NEXT", "ABOUT",
    "DESCRIBES",
]  # fmt: skip


def machine_node_id(machine_id: int) -> str:
    return f"M{machine_id:03d}"


# Recommended replacement interval per component (synthetic; used in manuals/SOPs)
MAINTENANCE_INTERVAL_DAYS = {"comp1": 180, "comp2": 150, "comp3": 210, "comp4": 120}

# Typical replacement time per component, hours (synthetic; SOP time estimate)
REPLACEMENT_HOURS = {"comp1": 4, "comp2": 3, "comp3": 6, "comp4": 2}

DOC_TYPES = ["manual", "troubleshooting", "sop", "incident_report"]
