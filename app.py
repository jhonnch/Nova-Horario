# -----------------------------
# NOVA HORARIO - V4.3 (FIXES: keys, días, solver mañana/tarde, solver estable)
# Compatible con Python 3.9
# -----------------------------
import json
import uuid
import time
from io import BytesIO
from datetime import datetime

import streamlit as st
from ortools.sat.python import cp_model
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

# Configuración de página
st.set_page_config(page_title="NOVA HORARIO", layout="wide", page_icon="📅")

# Constantes
DAYS = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sa"]
DAY_FULL = {
    "Lu": "Lunes", "Ma": "Martes", "Mi": "Miércoles",
    "Ju": "Jueves", "Vi": "Viernes", "Sa": "Sábado",
}
DAY_IDX = {d: i for i, d in enumerate(DAYS)}

# Patrón típico sugerido para default-day (si quieres, lo ajustamos)
NEXT_DAY = {"Lu": "Mi", "Ma": "Ju", "Mi": "Vi", "Ju": "Vi", "Vi": "Sa", "Sa": "Lu"}

# Paleta de colores VIBRANTES
COLORS = [
    (255, 179, 186), (186, 255, 201), (186, 225, 255),
    (255, 255, 186), (255, 223, 186), (224, 187, 228),
    (149, 238, 238), (255, 218, 245), (210, 210, 210)
]


# -----------------------------
# Auxiliares
# -----------------------------
def overlaps(a, b) -> bool:
    # a,b: (day_idx, start_min, end_min)
    return a[0] == b[0] and not (a[2] <= b[1] or b[2] <= a[1])


def allowed_start_hours():
    # 07..21, excluye 13
    return [h for h in range(7, 22) if h != 13]


def allowed_end_hours(day: str, start_h: int):
    # Viernes/Sábado: 1/2/3 horas; otros: 2/3 horas
    durs = [1, 2, 3] if day in ("Vi", "Sa") else [2, 3]
    ends = []
    for dur in durs:
        end_h = start_h + dur
        # sin pasar 22
        if end_h > 22:
            continue
        # prohibido cruzar 13-14
        if start_h < 14 and end_h > 13:
            continue
        ends.append(end_h)
    return ends


def suggest_day(used_days_in_order):
    # Intenta seguir NEXT_DAY, si no, primer día libre; si no hay, Lu.
    used = set(used_days_in_order)
    if not used_days_in_order:
        return "Lu"
    last = used_days_in_order[-1]
    nxt = NEXT_DAY.get(last)
    if nxt and nxt not in used:
        return nxt
    for d in DAYS:
        if d not in used:
            return d
    return "Lu"


# -----------------------------
# Estado / Keys
# -----------------------------
def purge_keys_with_prefix(prefix: str):
    # Borrar claves de widgets desde session_state (importante para no “arrastrar” estado viejo)
    to_del = [k for k in list(st.session_state.keys()) if k.startswith(prefix)]
    for k in to_del:
        del st.session_state[k]


def blocks_key(course_i: int, opt_j: int) -> str:
    return f"c{course_i}_o{opt_j}_blocks"


def add_block(course_i: int, opt_j: int):
    bk = blocks_key(course_i, opt_j)
    st.session_state.setdefault(bk, [])

    new_id = str(uuid.uuid4())
    used_days_in_order = [b.get("day", "Lu") for b in st.session_state[bk] if b.get("day")]
    default_day = suggest_day(used_days_in_order)

    # defaults válidos
    start_h = 7
    end_opts = allowed_end_hours(default_day, start_h)
    end_h = end_opts[0] if end_opts else 9

    st.session_state[bk].append({"id": new_id, "day": default_day, "start": start_h, "end": end_h})


def delete_block(course_i: int, opt_j: int, block_id: str):
    bk = blocks_key(course_i, opt_j)
    if bk in st.session_state:
        st.session_state[bk] = [b for b in st.session_state[bk] if b.get("id") != block_id]
        # ahora sí coincide con nuestras keys de widgets:
        purge_keys_with_prefix(f"{bk}_{block_id}_")


def clear_blocks(course_i: int, opt_j: int):
    bk = blocks_key(course_i, opt_j)
    st.session_state[bk] = []
    purge_keys_with_prefix(f"{bk}_")


# -----------------------------
# Importar / Exportar JSON
# -----------------------------
def export_to_json():
    data = {
        "version": 43,
        "strategy": st.session_state.get("strategy", "Equilibrado"),
        "timestamp": datetime.now().isoformat(),
        "courses": []
    }
    for i in range(7):
        c_name = (st.session_state.get(f"cname{i}", "") or "").strip()
        oc_val = int(st.session_state.get(f"oc{i}", 3) or 3)
        course_obj = {"index": i, "name": c_name, "opt_count": oc_val, "options": []}

        for j in range(oc_val):
            o_name = st.session_state.get(f"oname{i}_{j}", f"Opción {j+1}")
            is_virt = bool(st.session_state.get(f"ovirt{i}_{j}", False))
            blocks = st.session_state.get(blocks_key(i, j), [])
            course_obj["options"].append(
                {"index": j, "name": o_name, "virtual": is_virt, "blocks": blocks}
            )
        data["courses"].append(course_obj)

    return json.dumps(data, indent=2, ensure_ascii=False)


def import_from_json(json_content):
    try:
        data = json.loads(json_content)
        st.session_state["strategy"] = data.get("strategy", "Equilibrado")

        for c in data.get("courses", []):
            i = c.get("index")
            if i is None or not (0 <= i < 7):
                continue

            st.session_state[f"cname{i}"] = c.get("name", "")
            st.session_state[f"oc{i}"] = int(c.get("opt_count", 3) or 3)

            for opt in c.get("options", []):
                j = int(opt.get("index", 0) or 0)
                st.session_state[f"oname{i}_{j}"] = opt.get("name", f"Opción {j+1}")
                st.session_state[f"ovirt{i}_{j}"] = bool(opt.get("virtual", False))

                blks = opt.get("blocks", [])
                for b in blks:
                    if "id" not in b:
                        b["id"] = str(uuid.uuid4())
                st.session_state[blocks_key(i, j)] = blks
                purge_keys_with_prefix(f"{blocks_key(i, j)}_")

        st.success("✅ Datos cargados.")
        time.sleep(0.2)
        st.rerun()
    except Exception as e:
        st.error(f"Error al importar: {str(e)}")


# -----------------------------
# UI: Editor de Bloques
# -----------------------------
def ui_blocks_editor(course_i: int, opt_j: int):
    virt_key = f"ovirt{course_i}_{opt_j}"
    st.checkbox("📡 ¿Es curso Virtual?", key=virt_key)

    bk = blocks_key(course_i, opt_j)
    st.session_state.setdefault(bk, [])
    blocks = st.session_state[bk]

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("➕ Hora", key=f"add_{course_i}_{opt_j}"):
            add_block(course_i, opt_j)
            st.rerun()
    with col2:
        if blocks and st.button("🗑 Limpiar", key=f"clr_{course_i}_{opt_j}"):
            clear_blocks(course_i, opt_j)
            st.rerun()

    updated_blocks = []

    for blk in blocks:
        b_id = blk["id"]

        # DÍAS disponibles: excluir los usados por otros bloques (dinámico, por bloque)
        other_days = {b.get("day") for b in blocks if b.get("id") != b_id}
        other_days.discard(None)
        avail_days = [d for d in DAYS if d not in other_days]
        if not avail_days:
            # no debería pasar si hay <=6 bloques, pero por seguridad:
            avail_days = DAYS[:]

        # día actual válido
        curr_day = blk.get("day", "Lu")
        if curr_day not in avail_days:
            curr_day = avail_days[0]

        with st.container():
            c1, c2, c3, c4 = st.columns([2, 2, 2, 1])

            # Keys con namespace completo -> permite borrar y evita colisiones
            k_day = f"{bk}_{b_id}_d"
            k_start = f"{bk}_{b_id}_s"
            k_end = f"{bk}_{b_id}_e"

            with c1:
                new_day = st.selectbox(
                    "Día",
                    avail_days,
                    index=avail_days.index(curr_day),
                    key=k_day,
                    format_func=lambda x: DAY_FULL.get(x, x),
                )

            with c2:
                opts_s = allowed_start_hours()
                curr_s = blk.get("start", 7)
                if curr_s not in opts_s:
                    curr_s = opts_s[0]
                new_start = st.selectbox(
                    "Inicio",
                    opts_s,
                    index=opts_s.index(curr_s),
                    key=k_start,
                    format_func=lambda x: f"{x:02d}:00",
                )

            with c3:
                opts_e = allowed_end_hours(new_day, int(new_start))
                if not opts_e:
                    # fallback ultra defensivo
                    opts_e = [min(22, int(new_start) + 1)]

                # intenta mantener duración previa
                prev_dur = int(blk.get("end", int(new_start) + 2)) - int(blk.get("start", int(new_start)))
                desired = int(new_start) + max(1, prev_dur)
                curr_e = desired if desired in opts_e else opts_e[0]

                new_end = st.selectbox(
                    "Fin",
                    opts_e,
                    index=opts_e.index(curr_e),
                    key=k_end,
                    format_func=lambda x: f"{x:02d}:00",
                )

            with c4:
                st.write("")
                st.write("")
                if st.button("❌", key=f"del_{bk}_{b_id}"):
                    delete_block(course_i, opt_j, b_id)
                    st.rerun()

            blk.update({"day": new_day, "start": int(new_start), "end": int(new_end)})
            updated_blocks.append(blk)
            st.divider()

    st.session_state[bk] = updated_blocks
    return updated_blocks


# -----------------------------
# SOLVER (CP-SAT / OR-Tools)
# -----------------------------
def solve_schedule(courses_data, strategy):
    model = cp_model.CpModel()
    picks = {}

    # 1) Variables: 1 opción por curso
    for c_idx, course in enumerate(courses_data):
        opts = course["options"]
        vars_course = []
        for o_idx in range(len(opts)):
            v = model.NewBoolVar(f"pick_c{c_idx}_o{o_idx}")
            picks[(c_idx, o_idx)] = v
            vars_course.append(v)
        model.Add(sum(vars_course) == 1)

    # 2) Choques entre cursos
    for c1 in range(len(courses_data)):
        for o1 in range(len(courses_data[c1]["options"])):
            b1_list = courses_data[c1]["options"][o1]["blocks"]
            for c2 in range(c1 + 1, len(courses_data)):
                for o2 in range(len(courses_data[c2]["options"])):
                    b2_list = courses_data[c2]["options"][o2]["blocks"]

                    conflict = False
                    for ba in b1_list:
                        sa = (DAY_IDX[ba["day"]], int(ba["start"]) * 60, int(ba["end"]) * 60)
                        for bb in b2_list:
                            sb = (DAY_IDX[bb["day"]], int(bb["start"]) * 60, int(bb["end"]) * 60)
                            if overlaps(sa, sb):
                                conflict = True
                                break
                        if conflict:
                            break

                    if conflict:
                        model.Add(picks[(c1, o1)] + picks[(c2, o2)] <= 1)

    penalties = []

    # 3A) Penalidad “Mañana/Tarde” (CORRECTA):
    # Como los horarios (s,e) son constantes, se calcula en Python y se penaliza el pick.
    if strategy in ("Mañana", "Tarde"):
        limit_m = 13 * 60
        limit_t = 14 * 60
        for c_idx, course in enumerate(courses_data):
            for o_idx, opt in enumerate(course["options"]):
                bad = False
                for blk in opt["blocks"]:
                    s = int(blk["start"]) * 60
                    e = int(blk["end"]) * 60
                    if strategy == "Mañana" and e > limit_m:
                        bad = True
                        break
                    if strategy == "Tarde" and s < limit_t:
                        bad = True
                        break
                if bad:
                    penalties.append(1000 * picks[(c_idx, o_idx)])

    # 3B) Métrica de “huecos” por día + días libres + mezcla virtual/presencial
    BIG_M = 24 * 60

    for d_idx in range(len(DAYS)):
        # recolecta todos los “segmentos” que podrían activarse ese día
        segs = []
        for c_idx, course in enumerate(courses_data):
            for o_idx, opt in enumerate(course["options"]):
                p = picks[(c_idx, o_idx)]
                is_virt = bool(opt.get("virtual", False))
                for blk in opt["blocks"]:
                    if DAY_IDX[blk["day"]] != d_idx:
                        continue
                    s = int(blk["start"]) * 60
                    e = int(blk["end"]) * 60
                    segs.append((s, e, is_virt, p))

        if not segs:
            continue

        # day_active: si hay al menos 1 clase seleccionada ese día
        day_active = model.NewBoolVar(f"active_d{d_idx}")
        model.Add(sum(p for _, _, _, p in segs) >= 1).OnlyEnforceIf(day_active)
        model.Add(sum(p for _, _, _, p in segs) == 0).OnlyEnforceIf(day_active.Not())

        if strategy == "Días Libres":
            penalties.append(2000 * day_active)

        # min_s / max_e para medir span del día
        min_s = model.NewIntVar(0, 1440, f"min_s_{d_idx}")
        max_e = model.NewIntVar(0, 1440, f"max_e_{d_idx}")

        # si el día no está activo, fijas min/max en 0 por limpieza
        model.Add(min_s == 0).OnlyEnforceIf(day_active.Not())
        model.Add(max_e == 0).OnlyEnforceIf(day_active.Not())

        # bounding con BIG_M
        for s, _, _, p in segs:
            model.Add(min_s <= s + BIG_M * (1 - p))
        for _, e, _, p in segs:
            model.Add(max_e >= e - BIG_M * (1 - p))

        dur_sum = sum((e - s) * p for s, e, _, p in segs)

        span = model.NewIntVar(0, 1440, f"span_{d_idx}")
        model.Add(span == max_e - min_s).OnlyEnforceIf(day_active)
        model.Add(span == 0).OnlyEnforceIf(day_active.Not())

        gap = model.NewIntVar(0, 1440, f"gap_{d_idx}")
        model.Add(gap == span - dur_sum).OnlyEnforceIf(day_active)
        model.Add(gap == 0).OnlyEnforceIf(day_active.Not())
        penalties.append(gap)

        # Anti-sandwich virtual/presencial: penaliza días “mixtos”
        virt_load = sum((e - s) * p for s, e, v, p in segs if v)
        pres_load = sum((e - s) * p for s, e, v, p in segs if not v)

        has_virt = model.NewBoolVar(f"has_virt_{d_idx}")
        has_pres = model.NewBoolVar(f"has_pres_{d_idx}")

        model.Add(virt_load >= 1).OnlyEnforceIf(has_virt)
        model.Add(virt_load == 0).OnlyEnforceIf(has_virt.Not())
        model.Add(pres_load >= 1).OnlyEnforceIf(has_pres)
        model.Add(pres_load == 0).OnlyEnforceIf(has_pres.Not())

        is_mixed = model.NewBoolVar(f"mixed_{d_idx}")
        # equivalencia estándar: mixed <-> (has_virt AND has_pres)
        model.AddBoolAnd([has_virt, has_pres]).OnlyEnforceIf(is_mixed)
        model.AddBoolOr([has_virt.Not(), has_pres.Not()]).OnlyEnforceIf(is_mixed.Not())

        penalties.append(500 * is_mixed)

    model.Minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result = []
        for c_idx, course in enumerate(courses_data):
            for o_idx, opt in enumerate(course["options"]):
                if solver.Value(picks[(c_idx, o_idx)]) == 1:
                    result.append({
                        "course_idx": c_idx,
                        "course": course["name"],
                        "option": opt.get("name", f"Opción {o_idx+1}"),
                        "virtual": bool(opt.get("virtual", False)),
                        "blocks": opt["blocks"]
                    })
        return result

    return None


# -----------------------------
# Generadores HTML / PDF
# -----------------------------
def render_html_schedule(schedule):
    html = """
    <style>
        .sched-table { width:100%; border-collapse:collapse; font-family:'Segoe UI', sans-serif; font-size:12px; }
        .sched-table td, .sched-table th { padding:0; margin:0; border: 1px solid #e0e0e0; height: 40px; }
        .sched-table th { background-color: #f8f9fa; color: #333; font-weight: 600; padding: 8px; text-align: center; }
        .time-cell {
            vertical-align: top; text-align: right; padding-right: 5px; color: #888; font-size: 11px;
            border-right: 2px solid #ddd; width: 50px; position: relative;
        }
        .time-label { position: relative; top: -8px; background: white; padding: 0 2px; }
        .class-block {
            border-radius: 4px; padding: 4px 6px; margin: 1px; height: calc(100% - 4px);
            box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 11px; line-height: 1.2; color: #333; overflow: hidden;
        }
        .virt-badge { float: right; font-size: 14px; }
        .lunch-row td { background: repeating-linear-gradient(45deg, #f9f9f9, #f9f9f9 10px, #eee 10px, #eee 20px); height: 20px; }
    </style>
    <div style="overflow-x:auto;"><table class="sched-table"><thead><tr><th style="border:none;"></th>
    """
    for d in DAYS:
        html += f"<th>{DAY_FULL[d]}</th>"
    html += "</tr></thead><tbody>"

    grid = {d: {} for d in range(len(DAYS))}
    for item in schedule:
        c_idx = item["course_idx"]
        color = COLORS[c_idx % len(COLORS)]
        rgb = f"rgb({color[0]},{color[1]},{color[2]})"
        for blk in item["blocks"]:
            d_idx = DAY_IDX[blk["day"]]
            s_h, e_h = int(blk["start"]), int(blk["end"])
            dur = max(1, e_h - s_h)
            icon = "📡" if item["virtual"] else "🏛"
            content = (
                f"<div class='class-block' style='background:{rgb};'>"
                f"<span class='virt-badge'>{icon}</span>"
                f"<b>{item['course']}</b><br>{item['option']}</div>"
            )
            grid[d_idx][s_h] = {"rows": dur, "html": content}
            for offset in range(1, dur):
                grid[d_idx][s_h + offset] = "SKIP"

    for h in range(7, 23):
        if h == 13:
            html += "<tr class='lunch-row'><td class='time-cell'><span class='time-label'>13:00</span></td><td colspan='6'></td></tr>"
            continue
        html += f"<tr><td class='time-cell'><span class='time-label'>{h:02d}:00</span></td>"
        for d in range(len(DAYS)):
            cell = grid[d].get(h)
            if cell == "SKIP":
                continue
            if cell:
                html += f"<td rowspan='{cell['rows']}'>{cell['html']}</td>"
            else:
                html += "<td></td>"
        html += "</tr>"

    html += "</tbody></table></div>"
    return html


def create_pdf(schedule):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(letter))
    width, height = landscape(letter)

    margin_x, margin_y = 40, 40
    grid_w, grid_h = width - 2 * margin_x, height - 100
    col_w, row_h = grid_w / len(DAYS), grid_h / 15
    start_y = height - 80

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin_x, height - 30, "NOVA HORARIO - Optimizado")

    c.setFont("Helvetica-Bold", 12)
    for i, d in enumerate(DAYS):
        x = margin_x + i * col_w
        c.drawCentredString(x + col_w / 2, start_y + 10, DAY_FULL[d])
        c.rect(x, start_y, col_w, -grid_h, stroke=1, fill=0)

    c.setFont("Helvetica", 8)
    c.setStrokeColorRGB(0.8, 0.8, 0.8)

    for h_idx, h in enumerate(range(7, 23)):
        y = start_y - h_idx * row_h
        c.line(margin_x, y, margin_x + grid_w, y)
        c.drawRightString(margin_x - 5, y - 3, f"{h:02d}:00")
        if h == 13:
            c.setFillColorRGB(0.9, 0.9, 0.9)
            c.rect(margin_x, y - row_h, grid_w, row_h, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)

    for item in schedule:
        color = COLORS[item["course_idx"] % len(COLORS)]
        r, g, b = [x / 255.0 for x in color]
        for blk in item["blocks"]:
            d_idx = DAY_IDX[blk["day"]]
            h_start, h_end = int(blk["start"]) - 7, int(blk["end"]) - 7
            x = margin_x + d_idx * col_w
            y_top = start_y - h_start * row_h
            y_bot = start_y - h_end * row_h

            c.setFillColorRGB(r, g, b)
            c.rect(x, y_bot, col_w, y_top - y_bot, stroke=1, fill=1)

            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x + 5, y_top - 12, (item["course"] or "")[:18])
            c.setFont("Helvetica", 8)
            modalidad = "Virtual" if item.get("virtual") else "Presencial"
            c.drawString(x + 5, y_top - 24, f"{item['option']} ({modalidad})")

    c.save()
    buffer.seek(0)
    return buffer


# -----------------------------
# APP UI
# -----------------------------
def main():
    with st.sidebar:
        st.title("⚙️ Configuración")
        st.subheader("📂 Gestión de Datos")

        up = st.file_uploader("Cargar JSON", type=["json"])
        if up is not None:
            if st.button("Restaurar Datos"):
                import_from_json(up.getvalue().decode("utf-8"))

        st.download_button("💾 Guardar JSON", export_to_json(), "horario_v4.json", "application/json")

        # Botón para descargar el código actual (por si necesitas nube)
        try:
            with open("app.py", "rb") as f:
                st.download_button("⬇️ Descargar app.py", data=f, file_name="app.py", mime="text/x-python")
        except Exception:
            pass

        st.divider()
        st.subheader("🎯 Objetivo")
        st.selectbox("Estrategia", ["Equilibrado", "Días Libres", "Mañana", "Tarde"], key="strategy")
        st.caption("Nota: Mañana penaliza bloques que terminen > 13:00; Tarde penaliza bloques que inicien < 14:00.")

    st.title("🎓 NOVA HORARIO")
    courses_data = []

    for i in range(7):
        cname = (st.session_state.get(f"cname{i}", "") or "").strip()
        label = f"Curso {i+1}: {cname}" if cname else f"Curso {i+1}"

        with st.expander(label, expanded=False):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.text_input("Nombre", key=f"cname{i}")
            with c2:
                st.number_input("Opciones", 1, 10, key=f"oc{i}")

            oc = int(st.session_state.get(f"oc{i}", 3) or 3)

            has_content = False
            curr_opts = []
            for j in range(oc):
                st.markdown(f"**Opción {j+1}**")
                st.text_input("Profesor/Sección", key=f"oname{i}_{j}")

                blocks = ui_blocks_editor(i, j)
                if blocks:
                    has_content = True
                    curr_opts.append({
                        "name": st.session_state.get(f"oname{i}_{j}", f"Opción {j+1}"),
                        "virtual": bool(st.session_state.get(f"ovirt{i}_{j}", False)),
                        "blocks": blocks
                    })

            if cname and has_content:
                courses_data.append({"name": cname, "options": curr_opts})

    st.divider()

    if st.button("🚀 OPTIMIZAR", type="primary", use_container_width=True):
        if not courses_data:
            st.error("Agrega cursos primero.")
            return

        with st.spinner("Optimizando..."):
            res = solve_schedule(courses_data, st.session_state.get("strategy", "Equilibrado"))

        if res:
            st.success("✅ ¡Hecho!")
            st.markdown(render_html_schedule(res), unsafe_allow_html=True)
            st.download_button("📄 PDF", create_pdf(res), "Horario.pdf", "application/pdf")
        else:
            st.error("❌ Conflicto de horarios (no hay combinación sin choques).")


if __name__ == "__main__":
    main()