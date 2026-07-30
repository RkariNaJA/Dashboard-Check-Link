"""Seeds 14 days of demo click history for the demo links (sample_mode only).

On the real server this never runs - real clicks are recorded by the
/go/<slug> redirect the moment someone uses a short link.
"""
import random
from datetime import date, timedelta

from db import get_conn

PER_DAY = {          # slug -> rough clicks per weekday
    "hr-form": 18,
    "it-service-desk": 9,
    "report-portal": 25,
    "room-booking": 5,
}


def seed_demo_hits(days):
    with get_conn() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM daily_hits").fetchone()[0]
        if existing:
            return
        links = conn.execute(
            "SELECT id, slug FROM links WHERE name LIKE '%(demo)%'"
        ).fetchall()
        rng = random.Random(42)
        today = date.today()
        for link in links:
            per_day = PER_DAY.get(link["slug"], 10)
            for back in range(days - 1, -1, -1):
                d = today - timedelta(days=back)
                n = max(0, int(rng.gauss(per_day, per_day * 0.4)))
                if d.weekday() >= 5:          # quiet weekends
                    n = n // 6
                for _ in range(n):
                    ip = f"10.0.1.{rng.randint(10, 60)}"
                    conn.execute(
                        """INSERT INTO daily_hits (link_id, day, ip, hits)
                           VALUES (?, ?, ?, 1)
                           ON CONFLICT(link_id, day, ip)
                           DO UPDATE SET hits = hits + 1""",
                        (link["id"], d.isoformat(), ip),
                    )
        print(f"[sample] seeded {days} days of demo clicks")
