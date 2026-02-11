from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..config import settings
from ..db import get_connection, release_connection

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    x_api_key: str = Header(None),
    token: str | None = Query(None, description="Dashboard access token"),
):
    expected = settings.ingest_api_key
    if not expected:
        raise HTTPException(status_code=500, detail="Server API key not configured")
    if x_api_key != expected and token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT time, device_id, metric_type, val "
            "FROM sensor_vitals ORDER BY time DESC LIMIT 20"
        )
        vitals = cursor.fetchall()

        cursor.execute(
            "SELECT time, device_id, ST_AsText(coords), accuracy "
            "FROM sensor_location ORDER BY time DESC LIMIT 20"
        )
        locations = cursor.fetchall()

        cursor.execute(
            "SELECT time, device_id, event_type, label, duration_sec, metadata "
            "FROM user_events ORDER BY time DESC LIMIT 20"
        )
        events = cursor.fetchall()

        def render_rows(rows):
            return "".join(
                f"<tr>{''.join(f'<td>{str(col)}</td>' for col in row)}</tr>" for row in rows
            )

        html = """
        <html>
          <head>
            <title>LAMP Dashboard</title>
            <style>
              body { font-family: Arial, sans-serif; margin: 24px; }
              table { border-collapse: collapse; width: 100%; margin-bottom: 24px; }
              th, td { border: 1px solid #ddd; padding: 8px; font-size: 12px; }
              th { background: #f3f3f3; text-align: left; }
              h2 { margin-top: 24px; }
            </style>
          </head>
          <body>
            <h1>LAMP Ingestion Dashboard</h1>
            <p>Latest 20 rows per table.</p>
            <h2>sensor_vitals</h2>
            <table>
              <tr><th>time</th><th>device_id</th><th>metric_type</th><th>val</th></tr>
        """
        html += render_rows(vitals)
        html += """
            </table>
            <h2>sensor_location</h2>
            <table>
              <tr><th>time</th><th>device_id</th><th>coords</th><th>accuracy</th></tr>
        """
        html += render_rows(locations)
        html += """
            </table>
            <h2>user_events</h2>
            <table>
              <tr><th>time</th><th>device_id</th><th>event_type</th><th>label</th><th>duration_sec</th><th>metadata</th></tr>
        """
        html += render_rows(events)
        html += """
            </table>
          </body>
        </html>
        """

        return HTMLResponse(content=html)
    except Exception:
        raise HTTPException(status_code=503, detail="Dashboard unavailable")
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)
