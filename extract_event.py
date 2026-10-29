import re
import json
from datetime import date, datetime, timedelta


MONTHS = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december"
)

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def clean_label_value(text, label_pattern):
    """Extract a value from a labelled line, allowing emoji before the label."""
    pattern = re.compile(
        rf"^[^\w\n]*\s*(?:{label_pattern})\s*:\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def parse_explicit_date(text):
    """Parse explicit dates, including ordinal dates such as 14th November 2026."""
    patterns = [
        # 14th November 2026, 14 November 2026
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTHS})\s+(\d{{4}})\b",
        # November 14th, 2026 or November 14, 2026
        rf"\b({MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(\d{{4}})\b",
        # Numeric date: interpret slash dates as day/month/year
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        # ISO date
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
    ]

    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue

        try:
            if index == 0:
                day, month_name, year = match.groups()
                return datetime.strptime(
                    f"{day} {month_name} {year}", "%d %B %Y"
                ).date()

            if index == 1:
                month_name, day, year = match.groups()
                return datetime.strptime(
                    f"{month_name} {day} {year}", "%B %d %Y"
                ).date()

            if index == 2:
                day, month, year = match.groups()
                return date(int(year), int(month), int(day))

            year, month, day = match.groups()
            return date(int(year), int(month), int(day))
        except ValueError:
            # Invalid explicit dates should not crash extraction.
            continue

    return None


def extract_date(text, reference_date):
    event_date = parse_explicit_date(text)
    if event_date:
        return event_date

    lower = text.lower()

    if re.search(r"\btoday\b", lower):
        return reference_date
    if re.search(r"\btomorrow\b", lower):
        return reference_date + timedelta(days=1)
    if re.search(r"\byesterday\b", lower):
        return reference_date - timedelta(days=1)

    weekday_match = re.search(
        r"\b(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lower,
    )
    if weekday_match:
        target_day = WEEKDAYS[weekday_match.group(2)]
        difference = (target_day - reference_date.weekday()) % 7
        if weekday_match.group(1):
            difference = difference or 7
        return reference_date + timedelta(days=difference)

    return None


def normalize_time(value):
    """Convert a recognized time to 24-hour HH:MM format."""
    value = value.lower().strip()
    value = re.sub(r"a\s*\.?\s*m\.?", "am", value)
    value = re.sub(r"p\s*\.?\s*m\.?", "pm", value)
    value = re.sub(r"(\d{1,2})\.(\d{2})", r"\1:\2", value)
    value = re.sub(r"(\d)(am|pm)$", r"\1 \2", value)
    value = re.sub(r"\s+", " ", value).strip()

    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            return datetime.strptime(value.upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def extract_times(text):
    """Extract times and normalize them; ignores invalid numeric matches."""
    pattern = re.compile(
        r"""
        (?<!\d)
        (?:
            \d{1,2}\s*[:.]\s*\d{2}
            \s*(?:a\s*\.?\s*m\.?|p\s*\.?\s*m\.?)?
            |
            \d{1,2}\s*(?:a\s*\.?\s*m\.?|p\s*\.?\s*m\.?)
        )
        (?!\d)
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    times = []
    for match in pattern.findall(text):
        normalized = normalize_time(match)
        if normalized is not None and normalized not in times:
            times.append(normalized)
    return times


def extract_event(message, reference_date=None):
    """
    Extract an event from one announcement message.

    Pass reference_date as a datetime.date representing the Telegram
    message's sent date when resolving relative dates such as 'today'.
    """
    text = message.strip().replace("\r\n", "\n")
    if not text:
        return None

    if reference_date is None:
        reference_date = date.today()

    lower = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 1. Title and event type
    first_line = lines[0] if lines else None
    title = re.sub(r"^[^\w]+", "", first_line).strip() if first_line else None
    title = title or None

    # If the first line is actually a labelled detail, don't treat it as a title.
    if title and re.match(
        r"^(date|time|venue|location|mode|eligibility|team size)\s*:",
        title,
        re.IGNORECASE,
    ):
        title = None

    if re.search(r"\b(competition|hackathon|ctf|tournament|contest)\b", lower):
        event_type = "competition"
    elif re.search(r"\b(exam|examination|midsem|mid-term)\b", lower):
        event_type = "exam"
    elif re.search(r"\b(quiz|test)\b", lower):
        event_type = "quiz"
    elif re.search(r"\b(assignment|coursework)\b", lower):
        event_type = "assignment"
    elif re.search(r"\bpresentation\b", lower):
        event_type = "presentation"
    elif re.search(r"\bproject\b", lower):
        event_type = "project"
    elif re.search(r"\b(class|lecture|meeting|session)\b", lower):
        event_type = "class"
    else:
        event_type = "other"

    # 2. Date
    event_date = extract_date(text, reference_date)

    # 3. Time
    times = extract_times(text)
    start_time = times[0] if times else None
    end_time = times[1] if len(times) > 1 else None

    # 4. Location and mode
    location = clean_label_value(text, r"location|venue|room")
    if location is None:
        informal_location = re.search(
            r"\b(?:held in|held at)\s+([^,\n.]+)",
            text,
            re.IGNORECASE,
        )
        if informal_location:
            location = informal_location.group(1).strip()

    explicit_mode = clean_label_value(text, r"mode|delivery mode")
    if explicit_mode:
        normalized_mode = explicit_mode.lower()
        if "hybrid" in normalized_mode or "both" in normalized_mode:
            mode = "hybrid"
        elif "online" in normalized_mode:
            mode = "online"
        elif any(term in normalized_mode for term in ("face-to-face", "face to face", "in person", "physical")):
            mode = "face_to_face"
        else:
            mode = "unknown"
    else:
        mentions_online = bool(re.search(
            r"\b(online|microsoft teams|google meet|webex|join meeting|meeting link)\b",
            lower,
        ))
        mentions_physical = bool(re.search(
            r"\b(face.to.face|f2f|in person|physical class|come to the class|held in|held at|venue|location)\b",
            lower,
        ))
        if mentions_online and mentions_physical:
            mode = "hybrid"
        elif mentions_online:
            mode = "online"
        elif mentions_physical:
            mode = "face_to_face"
        else:
            mode = "unknown"

    # 5. Online meeting details
    meeting_platform = None
    meeting_link = None
    meeting_id = None
    passcode = None

    if "teams.microsoft.com" in lower or "microsoft teams" in lower:
        meeting_platform = "Microsoft Teams"
    elif "meet.google.com" in lower or "google meet" in lower:
        meeting_platform = "Google Meet"
    elif "webex.com" in lower or "webex" in lower:
        meeting_platform = "Webex"

    link_match = re.search(r"https?://[^\s<>]+", text)
    if link_match:
        meeting_link = link_match.group().rstrip(".,;)")
        link_lower = meeting_link.lower()
        if "teams.microsoft.com" in link_lower:
            meeting_platform = "Microsoft Teams"
        elif "meet.google.com" in link_lower:
            meeting_platform = "Google Meet"
        elif "webex.com" in link_lower:
            meeting_platform = "Webex"

    id_match = re.search(
        r"\bmeeting\s*(?:id|number)\s*:\s*([A-Za-z0-9 -]+)",
        text,
        re.IGNORECASE,
    )
    if id_match:
        meeting_id = id_match.group(1).strip()

    passcode_match = re.search(
        r"\b(?:meeting\s+)?(?:passcode|password)\s*:\s*(\S+)",
        text,
        re.IGNORECASE,
    )
    if passcode_match:
        passcode = passcode_match.group(1).strip()

    # 6. Status
    # Canonical statuses: confirmed, cancelled, postponed, unknown.
    status = "unknown"
    status_reason = None

    cancelled = bool(re.search(
        r"\b(class|meeting|session|lecture|event|competition)\b.{0,40}"
        r"\b(cancelled|canceled|called off)\b|\b(no class|class is off)\b",
        lower,
    ))
    postponed = bool(re.search(
        r"\b(postponed|rescheduled|moved to next week|meet(?:ing)? next week instead)\b",
        lower,
    ))
    informal_postponement = bool(re.search(
        r"\b(this evening|tonight|today)\b.{0,150}"
        r"\b(meeting|class|session|lecture)\b.{0,60}\bnext week\b",
        lower,
        re.IGNORECASE | re.DOTALL,
    ))

    if cancelled:
        status = "cancelled"
        status_reason = "Message indicates cancellation"
    elif postponed or informal_postponement:
        status = "postponed"
        status_reason = "Message indicates a schedule change"
    elif (
        event_date is not None
        and (start_time is not None or mode in ("online", "hybrid", "face_to_face"))
    ):
        status = "confirmed"
        status_reason = "Announcement provides scheduled event details"

    # 7. Tasks and agenda
    tasks = []

    if re.search(r"\b(get your team together|form a team|assemble your team)\b", lower):
        tasks.append({
            "title": "Get your team together",
            "category": "team_preparation",
            "due_date": None,
        })

    if re.search(r"\b(register|registration|sign up)\b", lower):
        tasks.append({
            "title": "Check registration requirements",
            "category": "registration",
            "due_date": None,
        })

    if re.search(r"\b(go through|review|read|study|prepare)\b.{0,50}\bslides?\b", lower):
        tasks.append({
            "title": "Go through the slides",
            "category": "student_preparation",
            "due_date": None,
        })

    # Treat numbered lines following an Agenda heading as agenda tasks.
    agenda_match = re.search(
        r"(?im)^\s*[^\w\n]*agenda\s*:?\s*\n(?P<items>.*?)(?=\n\s*\n|\Z)",
        text,
    )
    if agenda_match:
        for item_match in re.finditer(
            r"(?m)^\s*\d+\s*[.)]\s*(.+?)\s*$",
            agenda_match.group("items"),
        ):
            item = re.sub(r"\s+", " ", item_match.group(1)).strip()
            if item:
                tasks.append({
                    "title": item,
                    "category": "class_agenda",
                    "due_date": None,
                })

    
    # Extract bullet points and warning messages from the announcement.
    for line in lines:
        cleaned = re.sub(r"^[\W_]+", "", line).strip()

        # Ignore empty lines and headings.
        if not cleaned or cleaned.lower().rstrip(":") in {
            "quiz format", "agenda"
        }:
            continue

        # Remove bullet markers, but retain the actual message.
        bullet_match = re.match(
            r"^(?:[-*•▪️]+)\s*(.+)$",
            line
        )

        if bullet_match:
            item = bullet_match.group(1).strip()
            item = re.sub(r"^[⚠️📢🗓⏰⏳\s]+", "", item)

            if re.search(
                r"\b(bring|prepare|review|read|study|"
                r"ensure|make sure|remember)\b",
                item,
                re.IGNORECASE
            ):
                tasks.append({
                    "title": item,
                    "category": "student_preparation",
                    "due_date": None
                })



    # 8. Additional notes

    # 8. Additional notes
    notes = []

    def add_note(note):
        """Add a non-empty note only if it has not already been recorded."""
        note = re.sub(r"\s+", " ", note).strip()
        if note and note not in notes:
            notes.append(note)

    # 1. Extract duration, supporting hours and minutes.
    duration_match = re.search(
        r"\bduration\s*:\s*(\d+|one|two|three|four|five|six)"
        r"\s*(hours?|hrs?|minutes?|mins?)\b",
        text,
        re.IGNORECASE,
    )

    if duration_match:
        amount = duration_match.group(1).lower()
        unit = duration_match.group(2).lower()

        number_words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
        }

        amount = str(number_words.get(amount, amount))

        if unit.startswith(("hour", "hr")):
            unit = "hour" if amount == "1" else "hours"
        else:
            unit = "minute" if amount == "1" else "minutes"

        add_note(f"Duration: {amount} {unit}")
    else:
        # Fallback for duration mentions without an explicit label.
        duration_match = re.search(
            r"\b(\d+)\s*(hours?|hrs?|minutes?|mins?)\b",
            lower,
        )

        if duration_match:
            amount = duration_match.group(1)
            unit = duration_match.group(2)

            if unit.startswith(("hour", "hr")):
                unit = "hour" if amount == "1" else "hours"
            else:
                unit = "minute" if amount == "1" else "minutes"

            add_note(f"Duration: {amount} {unit}")

    # 2. Extract generic labelled fields.
    eligibility = clean_label_value(text, r"eligibility")
    if eligibility:
        add_note(f"Eligibility: {eligibility}")

    team_size = clean_label_value(text, r"team size")
    if team_size:
        add_note(f"Team size: {team_size}")

    # 3. Labels already represented by structured event fields.
    known_labels = {
        "date",
        "time",
        "mode",
        "venue",
        "location",
        "room",
        "eligibility",
        "team size",
        "link",
        "meeting number",
        "meeting id",
        "password",
        "passcode",
    }

    # 4. Capture useful descriptive lines, bullets, and warnings.
    for line in lines:
        item = re.sub(r"^[\W_]+", "", line).strip()

        if not item:
            continue

        # Capture other labelled fields without duplicating known fields.
        label_match = re.match(
            r"^[^\w:]+?([A-Za-z][A-Za-z ]{1,30}):\s*(.+)$",
            line,
        )

        if label_match:
            label = label_match.group(1).strip().lower()
            value = label_match.group(2).strip()

            if label not in known_labels and value:
                add_note(
                    f"{label_match.group(1).strip()}: {value}"
                )

        # Capture bullet points as notes.
        bullet_match = re.match(
            r"^\s*[-*•▪️]+\s*(.+)$",
            line,
        )

        if bullet_match:
            note = bullet_match.group(1).strip()
            note = re.sub(
                r"^[⚠️‼️❗📢\s]+",
                "",
                note,
            ).strip()

            # Avoid duplicating duration and labelled-field notes.
            if note and not re.match(
                r"^duration\s*:",
                note,
                re.IGNORECASE,
            ):
                add_note(note)

        # Capture warning paragraphs, including unbulleted warnings.
        if re.match(r"^\s*[⚠️‼️❗]", line):
            warning = re.sub(
                r"^\s*[⚠️‼️❗]+\s*",
                "",
                line,
            ).strip()

            add_note(warning)

    needs_review = (
        event_date is None
        or status == "unknown"
        or mode == "unknown"
        or (mode in ("online", "hybrid") and meeting_link is None)
    )

    return {
        "event_type": event_type,
        "title": title,
        "date": event_date.isoformat() if event_date else None,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "mode": mode,
        "status": status,
        "status_reason": status_reason,
        "meeting": {
            "platform": meeting_platform,
            "link": meeting_link,
            "meeting_id": meeting_id,
            "passcode": passcode,
        },
        "tasks": tasks,
        "additional_notes": notes,
        "needs_review": needs_review,
        "original_message": text,
    }


if __name__ == "__main__":
    message = """
UBIQUITOUS COMPUTING MID-TERM EXAMINATION

Day & Date: Friday, 27 November 2026
Time: 8:00 PM – 10:00 PM
Location: L50 (DK1 & DK2)

Please ensure that this schedule does not conflict with other courses.

-
Assalamualaikum and good afternoon..

This week all classes had changed into Online Mode.

Unfortunately, this evening I have urgent matters that need to be addressed, so we'll have a meeting next week.

Today please do go through the slides first. So next week we will cover 2 topics. Thank you

"""
    result = extract_event(message, reference_date=date(2026, 10, 30))
    print(json.dumps(result, indent=4, ensure_ascii=False))
