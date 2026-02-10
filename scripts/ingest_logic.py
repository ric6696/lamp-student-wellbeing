import psycopg2
from psycopg2.extras import execute_values
import json

def ingest_batch(connection, batch_data):
    cursor = connection.cursor()
    device_id = batch_data['metadata']['device_id']

    # We use separate lists to batch-insert for efficiency
    vitals = []
    locations = []
    events = []

    for reading in batch_data['data']:
        r_type = reading.get('type')
        timestamp = reading.get('t')

        if r_type == 'vital':
            vitals.append((timestamp, device_id, reading['code'], reading['val']))

        elif r_type == 'gps':
            # ST_SetSRID converts lat/lon into a PostGIS point
            point = f"POINT({reading['lon']} {reading['lat']})"
            locations.append((timestamp, device_id, point, reading['acc']))

        elif r_type == 'event':
            events.append((timestamp, device_id, reading['label'], reading.get('val_text'), json.dumps(reading.get('metadata', {}))))

    # 1. Insert Vitals
    if vitals:
        execute_values(cursor,
            "INSERT INTO sensor_vitals (time, device_id, metric_type, val) VALUES %s", vitals)

    # 2. Insert Locations (using PostGIS casting)
    if locations:
        execute_values(cursor,
            "INSERT INTO sensor_location (time, device_id, coords, accuracy) VALUES %s",
            locations, template="(%s, %s, ST_GeogFromText(%s), %s)")

    # 3. Insert Events
    if events:
        execute_values(cursor,
            "INSERT INTO user_events (time, device_id, event_type, label, metadata) VALUES %s", events)

    connection.commit()
    cursor.close()
    print(f"Successfully ingested {len(vitals) + len(locations) + len(events)} records.")
