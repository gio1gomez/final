import json
import shutil
from datetime import datetime
from pathlib import Path
from math import ceil

from bson import ObjectId
from PIL import Image as PILImage
from pymongo import MongoClient
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
)


# =========================
# CONFIGURACION GENERAL
# =========================

MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB_NAME = "robot_grietas"
MONGO_COLLECTION_NAME = "detecciones"

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "informes_misiones"

# Solo se incluiran detecciones que tengan imagen existente
SKIP_IF_IMAGE_MISSING = True


# =========================
# UTILIDADES
# =========================

def connect_collection():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    db = client[MONGO_DB_NAME]
    return db[MONGO_COLLECTION_NAME]


def get_next_mission_number(collection):
    last_doc = collection.find_one(
        {"mission_number": {"$exists": True}},
        sort=[("mission_number", -1)]
    )

    last_number_db = 0
    if last_doc and "mission_number" in last_doc:
        last_number_db = int(last_doc["mission_number"])

    last_number_folder = 0
    if REPORTS_DIR.exists():
        for folder in REPORTS_DIR.iterdir():
            if folder.is_dir() and folder.name.startswith("mision_"):
                try:
                    number = int(folder.name.replace("mision_", ""))
                    last_number_folder = max(last_number_folder, number)
                except ValueError:
                    pass

    return max(last_number_db, last_number_folder) + 1


def resolve_image_path(image_path):
    if not image_path:
        return None

    path = Path(image_path)

    if path.is_absolute():
        return path

    return BASE_DIR / path


def fetch_pending_detections(collection):
    query = {
        "mission_number": {"$exists": False},
        "detection.crack_count": {"$gt": 0},
        "image_path": {"$exists": True, "$ne": ""}
    }

    docs = list(collection.find(query).sort("detected_at", 1))

    valid_docs = []

    for doc in docs:
        image_path = resolve_image_path(doc.get("image_path"))

        if image_path and image_path.exists():
            valid_docs.append(doc)
        else:
            print(f"Imagen no encontrada para deteccion {doc.get('_id')}: {doc.get('image_path')}")

            if not SKIP_IF_IMAGE_MISSING:
                valid_docs.append(doc)

    return valid_docs


def format_datetime(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def percent(value):
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return "--"


def safe_float(value, decimals=2):
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return "--"


def get_lane_column(doc):
    """
    Obtiene la columna/carril de la deteccion.
    El server guarda este dato como lane_column.
    Si el documento es antiguo y no tiene el campo, se muestra como 'Sin dato'.
    """
    value = doc.get("lane_column")

    if value is None:
        return "Sin dato"

    try:
        return int(value)
    except Exception:
        return str(value)


def lane_sort_key(lane):
    if isinstance(lane, int):
        return (0, lane)

    try:
        return (0, int(lane))
    except Exception:
        return (1, str(lane))


def build_lane_summary(detections):
    """
    Devuelve un resumen por carril:
    {
        carril: {
            "detections": cantidad de registros,
            "cracks": suma de detection.crack_count
        }
    }
    """
    summary = {}

    for doc in detections:
        lane = get_lane_column(doc)
        detection = doc.get("detection", {})
        crack_count = int(detection.get("crack_count", 0))

        if lane not in summary:
            summary[lane] = {
                "detections": 0,
                "cracks": 0
            }

        summary[lane]["detections"] += 1
        summary[lane]["cracks"] += crack_count

    return summary


def build_lane_summary_table(lane_summary):
    rows = [["Carril / columna", "Detecciones", "Grietas detectadas"]]

    for lane in sorted(lane_summary.keys(), key=lane_sort_key):
        values = lane_summary[lane]
        rows.append([
            str(lane),
            str(values["detections"]),
            str(values["cracks"])
        ])

    table = Table(rows, colWidths=[5 * cm, 5 * cm, 6 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EAF7")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))

    return table


def build_lane_bar_chart(lane_summary):
    """
    Grafica de barras simple con cantidad total de grietas por carril.
    """
    if not lane_summary:
        return Paragraph("No hay datos por carril para graficar.", getSampleStyleSheet()["Normal"])

    ordered_lanes = sorted(lane_summary.keys(), key=lane_sort_key)
    labels = [str(lane) for lane in ordered_lanes]
    crack_counts = [lane_summary[lane]["cracks"] for lane in ordered_lanes]

    max_value = max(crack_counts) if crack_counts else 0
    if max_value <= 0:
        max_value = 1

    value_step = max(1, ceil(max_value / 5))
    value_max = max_value + value_step

    drawing = Drawing(17 * cm, 9 * cm)

    chart = VerticalBarChart()
    chart.x = 1.2 * cm
    chart.y = 1.3 * cm
    chart.width = 14.5 * cm
    chart.height = 6.5 * cm

    chart.data = [crack_counts]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.boxAnchor = "ne"
    chart.categoryAxis.labels.dx = 6
    chart.categoryAxis.labels.dy = -2
    chart.categoryAxis.labels.angle = 0

    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = value_max
    chart.valueAxis.valueStep = value_step

    chart.bars[0].fillColor = colors.HexColor("#4A90E2")
    chart.barSpacing = 4
    chart.groupSpacing = 12

    drawing.add(chart)

    return drawing


def copy_evidence_images(detections, mission_dir):
    images_dir = mission_dir / "evidencias"
    images_dir.mkdir(parents=True, exist_ok=True)

    copied_paths = {}

    for index, doc in enumerate(detections, start=1):
        source_path = resolve_image_path(doc.get("image_path"))

        if not source_path or not source_path.exists():
            copied_paths[str(doc["_id"])] = None
            continue

        extension = source_path.suffix.lower() or ".jpg"
        new_name = f"deteccion_{index:03d}{extension}"
        target_path = images_dir / new_name

        shutil.copy2(source_path, target_path)

        copied_paths[str(doc["_id"])] = target_path

    return copied_paths


def make_report_image(image_path, max_width, max_height):
    if not image_path or not Path(image_path).exists():
        return Paragraph("Imagen no disponible.", getSampleStyleSheet()["Normal"])

    with PILImage.open(image_path) as img:
        width_px, height_px = img.size

    ratio = min(max_width / width_px, max_height / height_px)

    draw_width = width_px * ratio
    draw_height = height_px * ratio

    return Image(str(image_path), width=draw_width, height=draw_height)


def build_summary(detections):
    if not detections:
        return {
            "total_detections": 0,
            "total_cracks": 0,
            "avg_confidence": 0,
            "start_date": "--",
            "end_date": "--",
            "lane_count": 0,
        }

    total_cracks = 0
    confidences = []
    dates = []
    lanes = set()

    for doc in detections:
        detection = doc.get("detection", {})
        total_cracks += int(detection.get("crack_count", 0))
        lanes.add(get_lane_column(doc))

        if "confidence" in detection:
            confidences.append(float(detection["confidence"]))

        if isinstance(doc.get("detected_at"), datetime):
            dates.append(doc["detected_at"])

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    return {
        "total_detections": len(detections),
        "total_cracks": total_cracks,
        "avg_confidence": avg_confidence,
        "start_date": format_datetime(min(dates)) if dates else "--",
        "end_date": format_datetime(max(dates)) if dates else "--",
        "lane_count": len(lanes),
    }


def generate_pdf(mission_number, detections, copied_paths, mission_dir):
    pdf_path = mission_dir / f"informe_mision_{mission_number}.pdf"

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=1.4 * cm,
        leftMargin=1.4 * cm,
        topMargin=1.3 * cm,
        bottomMargin=1.3 * cm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleCustom",
        parent=styles["Title"],
        fontSize=24,
        leading=28,
        alignment=1,
        spaceAfter=14,
    )

    subtitle_style = ParagraphStyle(
        "SubtitleCustom",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        alignment=1,
        textColor=colors.HexColor("#444444"),
        spaceAfter=18,
    )

    heading_style = ParagraphStyle(
        "HeadingCustom",
        parent=styles["Heading2"],
        fontSize=15,
        leading=18,
        spaceBefore=10,
        spaceAfter=8,
    )

    normal_style = styles["Normal"]

    story = []

    story.append(Paragraph(f"Mision {mission_number}", title_style))
    story.append(Paragraph("Informe de detecciones de grietas", subtitle_style))

    summary = build_summary(detections)

    summary_data = [
        ["Fecha de generacion", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["Total de detecciones", str(summary["total_detections"])],
        ["Total de grietas detectadas", str(summary["total_cracks"])],
        ["Confianza promedio", percent(summary["avg_confidence"])],
        ["Columnas/carriles registrados", str(summary["lane_count"])],
        ["Primera deteccion", summary["start_date"]],
        ["Ultima deteccion", summary["end_date"]],
    ]

    summary_table = Table(summary_data, colWidths=[6 * cm, 10 * cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAEAEA")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))

    story.append(summary_table)
    story.append(Spacer(1, 0.5 * cm))

    for index, doc_item in enumerate(detections, start=1):
        telemetry = doc_item.get("telemetry", {})
        detection = doc_item.get("detection", {})
        lane_column = get_lane_column(doc_item)

        story.append(Paragraph(f"Deteccion {index}", heading_style))

        data = [
            ["Fecha y hora", format_datetime(doc_item.get("detected_at"))],
            ["Columna / carril", str(lane_column)],
            ["Temperatura", f"{safe_float(telemetry.get('temperature'))} °C"],
            ["Humedad", f"{safe_float(telemetry.get('humidity'))} %"],
            ["Acelerometro", (
                f"ax={safe_float(telemetry.get('ax'))}, "
                f"ay={safe_float(telemetry.get('ay'))}, "
                f"az={safe_float(telemetry.get('az'))}"
            )],
            ["Giroscopio", (
                f"gx={safe_float(telemetry.get('gx'))}, "
                f"gy={safe_float(telemetry.get('gy'))}, "
                f"gz={safe_float(telemetry.get('gz'))}"
            )],
            ["Cantidad de grietas", str(detection.get("crack_count", "--"))],
            ["Confianza del modelo", percent(detection.get("confidence", 0))],
            ["Imagen de evidencia", str(copied_paths.get(str(doc_item["_id"]), "--"))],
        ]

        table = Table(data, colWidths=[5 * cm, 11 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F0F0F0")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAAAAA")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))

        story.append(table)
        story.append(Spacer(1, 0.3 * cm))

        copied_image = copied_paths.get(str(doc_item["_id"]))
        report_image = make_report_image(
            copied_image,
            max_width=16 * cm,
            max_height=9 * cm
        )

        story.append(report_image)
        story.append(Spacer(1, 0.4 * cm))

        if index < len(detections):
            story.append(PageBreak())

    lane_summary = build_lane_summary(detections)

    if detections:
        story.append(PageBreak())

    story.append(Paragraph("Resumen de grietas por columna/carril", heading_style))
    story.append(Paragraph(
        "La siguiente tabla y grafica muestran la cantidad total de grietas detectadas en cada columna o carril registrado durante la mision.",
        normal_style
    ))
    story.append(Spacer(1, 0.4 * cm))

    story.append(build_lane_summary_table(lane_summary))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("Grafica de barras: grietas detectadas por carril", heading_style))
    story.append(build_lane_bar_chart(lane_summary))

    doc.build(story)

    return pdf_path


def write_manifest(mission_number, detections, copied_paths, pdf_path, mission_dir):
    manifest_path = mission_dir / f"mision_{mission_number}_metadata.json"

    data = {
        "mission_number": mission_number,
        "generated_at": datetime.now().isoformat(),
        "pdf_path": str(pdf_path),
        "total_detections": len(detections),
        "lane_summary": build_lane_summary(detections),
        "detections": []
    }

    for index, doc in enumerate(detections, start=1):
        data["detections"].append({
            "index": index,
            "mongo_id": str(doc["_id"]),
            "detected_at": format_datetime(doc.get("detected_at")),
            "lane_column": get_lane_column(doc),
            "telemetry": doc.get("telemetry", {}),
            "detection": doc.get("detection", {}),
            "original_image_path": doc.get("image_path"),
            "mission_image_path": str(copied_paths.get(str(doc["_id"]))),
        })

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return manifest_path


def mark_detections_as_reported(collection, mission_number, detections, copied_paths, pdf_path):
    now = datetime.now()

    for doc in detections:
        collection.update_one(
            {"_id": ObjectId(doc["_id"])},
            {
                "$set": {
                    "mission_number": mission_number,
                    "reported_at": now,
                    "mission_report_path": str(pdf_path),
                    "mission_image_path": str(copied_paths.get(str(doc["_id"]))),
                }
            }
        )


def main():
    print("Conectando a MongoDB...")
    collection = connect_collection()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    detections = fetch_pending_detections(collection)

    if not detections:
        print("No hay detecciones nuevas con imagen para generar informe.")
        print("Nota: las detecciones ya reportadas no se vuelven a incluir.")
        return

    mission_number = get_next_mission_number(collection)

    mission_dir = REPORTS_DIR / f"mision_{mission_number:03d}"
    mission_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generando informe de Mision {mission_number}...")
    print(f"Detecciones incluidas: {len(detections)}")

    copied_paths = copy_evidence_images(detections, mission_dir)
    pdf_path = generate_pdf(mission_number, detections, copied_paths, mission_dir)
    manifest_path = write_manifest(mission_number, detections, copied_paths, pdf_path, mission_dir)

    mark_detections_as_reported(
        collection,
        mission_number,
        detections,
        copied_paths,
        pdf_path
    )

    print("\nInforme generado correctamente.")
    print(f"Mision: {mission_number}")
    print(f"Carpeta: {mission_dir}")
    print(f"PDF: {pdf_path}")
    print(f"Metadata: {manifest_path}")


if __name__ == "__main__":
    main()
