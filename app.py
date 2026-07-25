from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from src.config_loader import load_programs
from src.parsers.registry import PARSERS
from src.service import load_results, refresh_all
from src.tracked_universities import TRACKED_UNIVERSITIES, filter_programs

st.set_page_config(page_title="Мониторинг поступления Димы", layout="wide")

STATUS_COLORS = {
    "green": "#22c55e",
    "yellow": "#eab308",
    "red": "#ef4444",
    "unknown": "#94a3b8",
}

STATUS_LABELS = {
    "green": "Проходит",
    "yellow": "Почти у границы",
    "red": "Не проходит",
    "unknown": "Нет данных",
}


def color_status(value: str) -> str:
    return STATUS_COLORS.get(value, STATUS_COLORS["unknown"])


def render_table(rows: list[dict]) -> None:
    if not rows:
        st.info("Нет сохранённых результатов. Нажмите «Обновить сейчас».")
        return

    df = pd.DataFrame(rows)
    display = df[
        [
            "university",
            "mega_direction",
            "program",
            "budget_places",
            "dima_score",
            "rank_consent",
            "rank_consent_priority1",
            "status",
            "probability",
            "probability_label",
            "parser",
            "error",
        ]
    ].rename(
        columns={
            "university": "ВУЗ",
            "mega_direction": "Мега-направление",
            "program": "Направление / программа",
            "budget_places": "Бюджет мест",
            "dima_score": "Балл Димы",
            "rank_consent": "Место (согласия)",
            "rank_consent_priority1": "Место (согласия + пр.1)",
            "status": "Статус",
            "probability": "Вероятность",
            "probability_label": "Оценка",
            "parser": "Парсер",
            "error": "Ошибка",
        }
    )

    def highlight_status(row: pd.Series) -> list[str]:
        status = row["Статус"]
        color = color_status(status)
        style = f"background-color: {color}; color: white; font-weight: 600;"
        return [style if col == "Место (согласия + пр.1)" else "" for col in row.index]

    styled = display.style.apply(highlight_status, axis=1).format({"Вероятность": "{:.0%}"})
    st.dataframe(styled, use_container_width=True, hide_index=True)


st.title("Мониторинг поступления Димы")
st.caption("Данные из `ВУЗы.xlsx`. Обновляйте списки периодически до 25.07.")

programs = filter_programs(load_programs())
col1, col2, col3 = st.columns(3)
col1.metric("Направлений", len(programs))
col2.metric("Вузов", len(TRACKED_UNIVERSITIES))
col3.metric("Парсеры готовы", "9 / 9")

if st.button("Обновить сейчас", type="primary"):
    with st.spinner("Загружаем конкурсные списки..."):
        rows = refresh_all(programs)
    ok = sum(1 for row in rows if not row.error)
    st.success(f"Готово: {ok} из {len(rows)} направлений обновлены.")

results = load_results()
if results.get("generated_at"):
    st.write(f"Последнее обновление: {results['generated_at']}")

render_table(results.get("rows", []))

with st.expander("Направления без парсера"):
    pending = sorted({p.parser for p in programs if p.parser not in PARSERS})
    st.write(", ".join(pending) if pending else "Все парсеры подключены.")

with st.expander("Как читать таблицу"):
    st.markdown(
        """
        - **Место (согласия)** — позиция Димы, если подать документы сейчас среди всех с согласием.
        - **Место (согласия + пр.1)** — среди тех, у кого есть согласие и 1-й приоритет на это направление.
        - **Зелёный** — попадает в бюджетные места, **жёлтый** — почти у границы, **красный** — не проходит.
        - **Вероятность** — оценка с учётом того, что до 25.07 люди могут менять согласия и приоритеты.
        """
    )
