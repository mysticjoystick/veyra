"""Veyra web applications.

Current surface:
  * dashboard app — a small FastAPI/Jinja2 dashboard that renders selective
    setup alerts from the frozen baseline. SIMULATION ONLY: it lists alerts
    and allows no trading, ordering, or account mutation.

The polished command-center arrives later; the dashboard is the first usable
read-only view over the strategy's alerts.
"""

from __future__ import annotations

from .dashboard import create_dashboard, dashboard_app

__all__ = ["create_dashboard", "dashboard_app"]