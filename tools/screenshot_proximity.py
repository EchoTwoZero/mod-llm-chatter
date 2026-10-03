"""Host mailbox client. Live player/NPC eligibility belongs to C++."""

import json
import logging
import time
import uuid

log = logging.getLogger(__name__)


def request_ticket(db, account_id, timeout_seconds):
    """Reserve one expiring account slot and wait for world-thread preflight."""
    token = uuid.uuid4().hex
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "INSERT IGNORE INTO llm_screenshot_proximity "
            "(account_id, request_token, state, expires_at) "
            "VALUES (%s, %s, 'consumed', NOW())",
            (account_id, token),
        )
        cursor.execute(
            "UPDATE llm_screenshot_proximity SET request_token=%s, "
            "state='requested', player_guid=NULL, observation=NULL, "
            "requested_at=NOW(), expires_at=DATE_ADD(NOW(), INTERVAL %s SECOND) "
            "WHERE account_id=%s AND (state='consumed' OR expires_at<=NOW())",
            (token, timeout_seconds, account_id),
        )
        reserved = cursor.rowcount == 1
        db.commit()
        if not reserved:
            log.info('Screenshot proximity slot busy; skipping')
            return None
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT state, player_guid FROM llm_screenshot_proximity "
                "WHERE account_id=%s AND request_token=%s AND expires_at>NOW()",
                (account_id, token),
            )
            row = cursor.fetchone()
            # End the read transaction so subsequent polls see server updates.
            db.commit()
            if not row:
                log.info('Screenshot proximity request expired or was replaced '
                         'before approval; check worldserver config reload')
                return None
            if row['state'] == 'consumed':
                log.info('Screenshot proximity rejected by server: player, '
                         'NPC eligibility, cooldown or active-scene gate')
                return None
            if row['state'] == 'ready' and row['player_guid']:
                return (account_id, token)
            time.sleep(0.1)
        log.info('Screenshot proximity preflight timed out waiting for '
                 'worldserver; check config reload and server availability')
        return None
    finally:
        cursor.close()


def publish_observation(db, ticket, description):
    """CAS publication: an expired/replaced/consumed ticket is never retried."""
    observation = json.dumps(description, ensure_ascii=False)
    if len(observation.encode('utf-8')) > 8192:
        log.warning('Screenshot proximity observation exceeds mailbox limit')
        return False
    cursor = db.cursor()
    try:
        cursor.execute(
            "UPDATE llm_screenshot_proximity SET state='observed', "
            "observation=%s WHERE account_id=%s AND request_token=%s "
            "AND state='ready' AND expires_at>NOW()",
            (observation, *ticket),
        )
        published = cursor.rowcount == 1
        db.commit()
        if not published:
            log.info('Screenshot proximity ticket expired or replaced; skipping')
        return published
    finally:
        cursor.close()
