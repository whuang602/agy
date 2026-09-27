#!/usr/bin/env bash
#
# AGY Custom Status Line - Session & Round Timer
#
# Computes total session duration and round duration (prompt to action completion).
# Can run standalone or as a segment within a unified status bar.
#

# --- Colors ---
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
GRAY="\033[90m"
RED="\033[31m"
RESET="\033[0m"

# --- Parse Arguments & Environment ---
SEGMENT_MODE=false
CUSTOM_TRANSCRIPT=""
CUSTOM_TRANSCRIPT_SPECIFIED=false
FORMAT_STYLE="${TIMER_STYLE:-verbose}"  # "verbose" (1h 02m 34s) or "digital" (01:02:34)
SUPPRESS_PENDING=false

while [ $# -gt 0 ]; do
    case "$1" in
        --segment)
            SEGMENT_MODE=true
            shift
            ;;
        --digital)
            FORMAT_STYLE="digital"
            shift
            ;;
        --verbose)
            FORMAT_STYLE="verbose"
            shift
            ;;
        --suppress-pending)
            SUPPRESS_PENDING=true
            shift
            ;;
        --transcript)
            CUSTOM_TRANSCRIPT_SPECIFIED=true
            if [ $# -ge 2 ]; then
                CUSTOM_TRANSCRIPT="$2"
                shift 2
            else
                shift
            fi
            ;;
        --transcript=*)
            CUSTOM_TRANSCRIPT_SPECIFIED=true
            CUSTOM_TRANSCRIPT="${1#*=}"
            shift
            ;;
        -h|--help)
            cat << 'EOF'
Usage: timer_status_bar.sh [OPTIONS]

AGY Custom Status Line - Session & Round Timer

Options:
  --segment           Output segment format: <COLORED>\t<RAW>
  --digital           Digital format (01:02:34)
  --verbose           Verbose format (1h 02m 34s) [default]
  --transcript PATH   Explicit transcript file to inspect
  --suppress-pending  Output nothing (exit 0) when pending or idle
  -h, --help          Show this help message
EOF
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

# --- Read stdin JSON payload if piped from AGY CLI ---
INPUT_JSON=""
if [ ! -t 0 ]; then
    INPUT_JSON=$(cat)
fi

# --- Guard jq presence: if jq is missing, render pending marker cleanly ---
if ! command -v jq >/dev/null 2>&1; then
    if [ "$SUPPRESS_PENDING" = true ]; then
        exit 0
    fi
    if [ "$SEGMENT_MODE" = true ]; then
        printf "%b\t%s\n" "${GRAY}󱎫 --:--${RESET} ${GRAY}│${RESET} ${GRAY}󰔛 --${RESET}" "󱎫 --:-- │ 󰔛 --"
    else
        echo -e "${GRAY}───[ ${RESET}${GRAY}󱎫 --:--${RESET} ${GRAY}│${RESET} ${GRAY}󰔛 --${RESET}${GRAY} ]────────────────────────────────────────────────────────────────────────${RESET}"
    fi
    exit 0
fi

# --- Validate and sanitize configuration knobs ---
if [[ "${AGY_STALL_SECS:-}" =~ ^[0-9]{1,9}$ ]]; then
    AGY_STALL_SECS=$(( 10#${AGY_STALL_SECS} ))
    [ "$AGY_STALL_SECS" -lt 1 ] && AGY_STALL_SECS=600
else
    AGY_STALL_SECS=600
fi

if [[ "${AGY_IDLE_GRACE_SECS:-}" =~ ^[0-9]{1,9}$ ]]; then
    AGY_IDLE_GRACE_SECS=$(( 10#${AGY_IDLE_GRACE_SECS} ))
else
    AGY_IDLE_GRACE_SECS=0
fi

if [[ "${AGY_FALLBACK_MAX_AGE_SECS:-}" =~ ^[0-9]{1,9}$ ]]; then
    AGY_FALLBACK_MAX_AGE_SECS=$(( 10#${AGY_FALLBACK_MAX_AGE_SECS} ))
else
    AGY_FALLBACK_MAX_AGE_SECS=300
fi

if [[ "${AGY_TRANSCRIPT_WINDOW_BYTES:-}" =~ ^[0-9]{1,9}$ ]]; then
    AGY_TRANSCRIPT_WINDOW_BYTES=$(( 10#${AGY_TRANSCRIPT_WINDOW_BYTES} ))
    [ "$AGY_TRANSCRIPT_WINDOW_BYTES" -le 0 ] && AGY_TRANSCRIPT_WINDOW_BYTES=524288
else
    AGY_TRANSCRIPT_WINDOW_BYTES=524288
fi
AGY_ROUND_TERMINAL_TYPES=$(echo "${AGY_ROUND_TERMINAL_TYPES:-}" | tr -cd 'a-zA-Z0-9_ ')

# Determine NOW (allow AGY_NOW override for deterministic tests)
if [[ "${AGY_NOW:-}" =~ ^[1-9][0-9]*$ ]]; then
    NOW=$(( 10#${AGY_NOW} ))
else
    NOW=$(date +%s)
fi

# --- Helper: Parse ISO-8601 UTC to Unix Epoch safely ---
# Rejects empty, null, or invalid strings to prevent midnight epoch bugs (RC-5/A2)
parse_iso_to_epoch() {
    local iso="$1"
    # Strict regex check for ISO-8601 prefix: YYYY-MM-DD[T ]HH:MM
    if ! [[ "$iso" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[T\ ][0-9]{2}:[0-9]{2} ]]; then
        return 1
    fi
    local epoch=""
    # GNU date (force UTC)
    epoch=$(date -u -d "$iso" +%s 2>/dev/null)
    if [ -n "$epoch" ] && [[ "$epoch" =~ ^[1-9][0-9]*$ ]]; then
        echo "$epoch"
        return 0
    fi
    # BSD date (macOS, force UTC)
    epoch=$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null)
    if [ -n "$epoch" ] && [[ "$epoch" =~ ^[1-9][0-9]*$ ]]; then
        echo "$epoch"
        return 0
    fi
    # Python3 fallback (pass iso via sys.argv[1] to prevent command injection)
    if command -v python3 >/dev/null 2>&1; then
        epoch=$(python3 -c "import sys, datetime; print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z', '+00:00')).timestamp()))" "$iso" 2>/dev/null)
        if [ -n "$epoch" ] && [[ "$epoch" =~ ^[1-9][0-9]*$ ]]; then
            echo "$epoch"
            return 0
        fi
    fi
    return 1
}

# --- Helper: Safely retrieve file modification time (mtime) in epoch seconds ---
get_file_mtime() {
    local f="$1"
    [ -f "$f" ] || return 1
    local mt
    mt=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null)
    if [[ "$mt" =~ ^[1-9][0-9]*$ ]]; then
        echo "$mt"
        return 0
    fi
    return 1
}

# --- Helper: Format seconds to continuous human-readable duration ---
format_duration() {
    local secs="$1"
    if [ -z "$secs" ] || [ "$secs" -le 0 ]; then
        if [ "$FORMAT_STYLE" = "digital" ]; then
            echo "00:00"
        else
            echo "<1s"
        fi
        return 0
    fi

    local h=$(( secs / 3600 ))
    local m=$(( (secs % 3600) / 60 ))
    local s=$(( secs % 60 ))

    if [ "$FORMAT_STYLE" = "digital" ]; then
        if [ "$h" -gt 0 ]; then
            printf "%02d:%02d:%02d" "$h" "$m" "$s"
        else
            printf "%02d:%02d" "$m" "$s"
        fi
    else
        if [ "$h" -gt 0 ]; then
            printf "%dh %02dm %02ds" "$h" "$m" "$s"
        elif [ "$m" -gt 0 ]; then
            printf "%dm %02ds" "$m" "$s"
        else
            printf "%ds" "$s"
        fi
    fi
}

# =============================================================================
# AUTHORITATIVE IDENTITY & TRANSCRIPT RESOLUTION
# =============================================================================
TRANSCRIPT=""
TRANSCRIPT_KEY=""
RESOLVE_MODE="none"

IS_AGY_HOST=false
if [[ "$INPUT_JSON" =~ [^[:space:]] ]]; then
    IS_AGY_HOST=true
fi

if [ "$CUSTOM_TRANSCRIPT_SPECIFIED" = true ]; then
    if [ -n "$CUSTOM_TRANSCRIPT" ] && [ -f "$CUSTOM_TRANSCRIPT" ] && [ ! -d "$CUSTOM_TRANSCRIPT" ]; then
        TRANSCRIPT="$CUSTOM_TRANSCRIPT"
        RESOLVE_MODE="custom"
        REAL_CUSTOM=$(readlink -f "$CUSTOM_TRANSCRIPT" 2>/dev/null || python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$CUSTOM_TRANSCRIPT" 2>/dev/null || echo "$CUSTOM_TRANSCRIPT")
        local_hash=$(printf '%s' "$REAL_CUSTOM" | cksum 2>/dev/null | awk '{print $1}')
        [ -z "$local_hash" ] && local_hash=$(echo "$REAL_CUSTOM" | tr -c 'a-zA-Z0-9_' '_' | cut -c1-32)
        TRANSCRIPT_KEY="p_${local_hash}"
    else
        RESOLVE_MODE="pending"
    fi
elif [ "$IS_AGY_HOST" = true ]; then
    CONV_RAW=$(echo "$INPUT_JSON" | jq -r '.conversation_id // .conversationId // .session_id // empty' 2>/dev/null)
    if [ -n "$CONV_RAW" ]; then
        # Strictly validate CONV_RAW: reject invalid IDs without lossy sanitization
        if [[ "$CONV_RAW" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
            CANDIDATE="$HOME/.gemini/antigravity-cli/brain/$CONV_RAW/.system_generated/logs/transcript.jsonl"
            if [ -f "$CANDIDATE" ]; then
                TRANSCRIPT="$CANDIDATE"
                TRANSCRIPT_KEY="$CONV_RAW"
                RESOLVE_MODE="cli"
            else
                # Authoritative identity: conversation ID exists in stdin, but transcript is not yet on disk
                # NEVER fall back to another conversation's transcript (RC-1)
                RESOLVE_MODE="pending"
                TRANSCRIPT_KEY="$CONV_RAW"
            fi
        else
            RESOLVE_MODE="pending"
        fi
    else
        # Stdin was piped from AGY CLI, so host is authoritative.
        # If conversation_id is absent, empty, or null, NEVER fall back to another conversation.
        RESOLVE_MODE="pending"
    fi
fi

# Fallback is permitted ONLY when running standalone (IS_AGY_HOST=false) without custom transcript
if [ "$IS_AGY_HOST" = false ] && [ "$RESOLVE_MODE" = "none" ] && [ -z "$TRANSCRIPT" ]; then
    CANDIDATE=$(ls -td "$HOME/.gemini/antigravity-cli/brain"/*/.system_generated/logs/transcript.jsonl 2>/dev/null | head -n 1)
    if [ -n "$CANDIDATE" ] && [ -f "$CANDIDATE" ]; then
        MTIME=$(get_file_mtime "$CANDIDATE")
        if [ -n "$MTIME" ] && (( NOW - MTIME <= AGY_FALLBACK_MAX_AGE_SECS )); then
            seg="${CANDIDATE#"$HOME/.gemini/antigravity-cli/brain/"}"
            seg="${seg%%/*}"
            if [[ "$seg" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
                TRANSCRIPT="$CANDIDATE"
                TRANSCRIPT_KEY="$seg"
                RESOLVE_MODE="fallback"
            fi
        fi
    fi
fi

# =============================================================================
# COMPUTE SESSION & ROUND TIMERS
# =============================================================================
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/agy-statusbar"
SESSION_CACHE=""
SESSION_EPOCH=""

if [ "$RESOLVE_MODE" = "pending" ] || [ "$RESOLVE_MODE" = "none" ] || [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    if [ "$SUPPRESS_PENDING" = true ]; then
        exit 0
    fi
    SESSION_TEXT="${GRAY}󱎫 --:--${RESET}"
    RAW_SESSION="󱎫 --:--"
    ROUND_TEXT="${GRAY}󰔛 --${RESET}"
    RAW_ROUND="󰔛 --"
else
    # Dedicated secure cache v2 directory (mode 0700, verify not symlink, owned by user)
    if [ ! -L "$CACHE_DIR" ]; then
        mkdir -p -m 0700 "$CACHE_DIR" 2>/dev/null
        if [ -d "$CACHE_DIR" ] && [ ! -L "$CACHE_DIR" ] && [ -O "$CACHE_DIR" ]; then
            chmod 0700 "$CACHE_DIR" 2>/dev/null
            SESSION_CACHE="${CACHE_DIR}/agy_sess_v2_${TRANSCRIPT_KEY}.start"
        fi
    fi

    # Opportunistic Garbage Collection (max once every 24h)
    if [ -n "$SESSION_CACHE" ]; then
        GC_STAMP="${CACHE_DIR}/.gc_stamp"
        DO_GC=false
        if [ ! -f "$GC_STAMP" ]; then
            DO_GC=true
        else
            STAMP_MTIME=$(get_file_mtime "$GC_STAMP")
            if [ -n "$STAMP_MTIME" ] && (( NOW - STAMP_MTIME > 86400 )); then
                DO_GC=true
            fi
        fi
        if [ "$DO_GC" = true ]; then
            touch "$GC_STAMP" 2>/dev/null
            find "$CACHE_DIR" -maxdepth 1 -name "agy_sb_tmp_*" -mmin +10 -delete 2>/dev/null || true
            find "$CACHE_DIR" -maxdepth 1 -name "agy_sess_v2_*.start" -mtime +30 -delete 2>/dev/null || true
            LEGACY_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/antigravity"
            if [ -d "$LEGACY_CACHE_DIR" ] && [ ! -L "$LEGACY_CACHE_DIR" ] && [ -O "$LEGACY_CACHE_DIR" ]; then
                find "$LEGACY_CACHE_DIR" -maxdepth 1 -name "agy_sess_*.start" -delete 2>/dev/null || true
            fi
        fi
    fi

    # Read start epoch from cache
    if [ -n "$SESSION_CACHE" ] && [ -f "$SESSION_CACHE" ] && [ ! -L "$SESSION_CACHE" ]; then
        CACHED_VAL=$(head -n 1 "$SESSION_CACHE" 2>/dev/null)
        if [[ "$CACHED_VAL" =~ ^[1-9][0-9]*$ ]] && (( CACHED_VAL <= NOW + 300 )); then
            SESSION_EPOCH="$CACHED_VAL"
        fi
    fi

    # If not in cache, read first line of transcript
    if [ -z "$SESSION_EPOCH" ]; then
        FIRST_ISO=$(head -n 1 "$TRANSCRIPT" 2>/dev/null | jq -r 'select(.created_at != null) | .created_at' 2>/dev/null)
        PARSED_EPOCH=$(parse_iso_to_epoch "$FIRST_ISO")
        if [ -n "$PARSED_EPOCH" ] && [[ "$PARSED_EPOCH" =~ ^[1-9][0-9]*$ ]] && (( PARSED_EPOCH <= NOW + 300 )); then
            SESSION_EPOCH="$PARSED_EPOCH"
            if [ -n "$SESSION_CACHE" ]; then
                TMP_CACHE=$(mktemp "${CACHE_DIR}/agy_sb_tmp_XXXXXX" 2>/dev/null)
                if [ -n "$TMP_CACHE" ] && [ -f "$TMP_CACHE" ]; then
                    chmod 0600 "$TMP_CACHE" 2>/dev/null
                    if echo "$SESSION_EPOCH" > "$TMP_CACHE" 2>/dev/null; then
                        chmod 0600 "$TMP_CACHE" 2>/dev/null
                        mv -f "$TMP_CACHE" "$SESSION_CACHE" 2>/dev/null || rm -f "$TMP_CACHE" 2>/dev/null
                    else
                        rm -f "$TMP_CACHE" 2>/dev/null
                    fi
                fi
            fi
        fi
    fi

    # Robust Snapshot Reader & Classifier
    FILE_SIZE=$(wc -c < "$TRANSCRIPT" 2>/dev/null | tr -d '[:space:]')
    [ -z "$FILE_SIZE" ] && FILE_SIZE=$(stat -c %s "$TRANSCRIPT" 2>/dev/null || stat -f %z "$TRANSCRIPT" 2>/dev/null | tr -d '[:space:]')
    [[ "$FILE_SIZE" =~ ^[0-9]+$ ]] || FILE_SIZE=0

    ROUND_JQ='
def tool_count: if (.tool_calls | type) == "array" then (.tool_calls | length) else 0 end;
def ts:
  if (.created_at|type)=="string" and (.created_at|length)>=16
     and .created_at[4:5]=="-" and .created_at[7:8]=="-"
     and (.created_at[10:11]=="T" or .created_at[10:11]==" ")
     and .created_at[13:14]==":" then
    (.created_at | explode | map(select(. >= 32)) | implode)
  else
    "-"
  end;
($tt | split(" ") | map(select(length > 0))) as $terminal
| reduce (inputs | fromjson? | objects | select((.type | type) == "string")) as $e
    ({state: "UNKNOWN", prompt: null, end: null, last: null};
     ($e | ts) as $t |
     (if $t != "-" then .last = $t else . end)
     | if $e.type == "USER_INPUT" then .state = "ACTIVE" | .prompt = (if $t != "-" then $t else null end) | .end = null
       elif $e.type == "PLANNER_RESPONSE" then
         if ($e | tool_count) == 0 then .state = "DONE" | .end = (if $t != "-" then $t else null end)
         else .state = "ACTIVE" | .end = null end
       elif any($terminal[]; . == $e.type) then .state = "INTERRUPTED" | .end = (if $t != "-" then $t else null end)
       else . end)
| [.state, (.prompt // "-"), (.end // "-"), (.last // "-")] | join("\u001f")'

    if [ "$FILE_SIZE" -gt "$AGY_TRANSCRIPT_WINDOW_BYTES" ]; then
        ROUND_OUT=$(tail -c "$AGY_TRANSCRIPT_WINDOW_BYTES" "$TRANSCRIPT" 2>/dev/null | jq -nrR --arg tt "$AGY_ROUND_TERMINAL_TYPES" "$ROUND_JQ" 2>/dev/null)
    else
        ROUND_OUT=$(cat "$TRANSCRIPT" 2>/dev/null | jq -nrR --arg tt "$AGY_ROUND_TERMINAL_TYPES" "$ROUND_JQ" 2>/dev/null)
    fi

    R_STATE="UNKNOWN"
    R_PROMPT_ISO="-"
    R_END_ISO="-"
    R_LAST_ISO="-"

    WINDOW_WAS_DONE=false
    if [ -n "$ROUND_OUT" ]; then
        IFS=$'\x1f' read -r R_STATE R_PROMPT_ISO R_END_ISO R_LAST_ISO <<< "$ROUND_OUT"
        [ "$R_STATE" = "DONE" ] && WINDOW_WAS_DONE=true
    fi

    TIER2_RECOVERED=false
    # Tier-2 lookup: if prompt not found in the window, reverse scan transcript to locate the most recent USER_INPUT
    if [ "$R_PROMPT_ISO" = "-" ] && [ "$FILE_SIZE" -gt "$AGY_TRANSCRIPT_WINDOW_BYTES" ]; then
        REV=()
        if command -v tac >/dev/null 2>&1; then
            REV=(tac)
        elif tail -r /dev/null >/dev/null 2>&1; then
            REV=(tail -r)
        fi

        if [ ${#REV[@]} -gt 0 ]; then
            GREP_ARGS=(-e '"USER_INPUT"' -e '"PLANNER_RESPONSE"')
            for t in $AGY_ROUND_TERMINAL_TYPES; do
                [ -n "$t" ] && GREP_ARGS+=(-e "\"$t\"")
            done

            T2_JQ='
def tc: if (.tool_calls|type)=="array" then (.tool_calls|length) else 0 end;
def ts:
  if (.created_at|type)=="string" and (.created_at|length)>=16
     and .created_at[4:5]=="-" and .created_at[7:8]=="-"
     and (.created_at[10:11]=="T" or .created_at[10:11]==" ")
     and .created_at[13:14]==":" then
    (.created_at | explode | map(select(. >= 32)) | implode)
  else
    "-"
  end;
($tt|split(" ")|map(select(length>0))) as $term
| label $done
| foreach (inputs|fromjson?|objects|select((.type|type)=="string")) as $e
    ({state:null, end:"-"};
     ($e | ts) as $t |
     if .state != null then .
     elif $e.type=="USER_INPUT"       then .state="ACTIVE"
     elif $e.type=="PLANNER_RESPONSE" then
       (if ($e|tc)==0 then .state="DONE" | .end=$t else .state="ACTIVE" end)
     elif any($term[]; .==$e.type)    then .state="INTERRUPTED" | .end=$t
     else . end;
     if $e.type=="USER_INPUT" then ([.state, ($e|ts), .end] | join("\u001f")), break $done else empty end)'

            T2_OUT=$(grep -F "${GREP_ARGS[@]}" "$TRANSCRIPT" 2>/dev/null | "${REV[@]}" 2>/dev/null \
                     | jq -nrR --arg tt "$AGY_ROUND_TERMINAL_TYPES" "$T2_JQ" 2>/dev/null)

            if [ -n "$T2_OUT" ]; then
                IFS=$'\x1f' read -r T2_STATE T2_PROMPT T2_END <<< "$T2_OUT"
                if [ -n "$T2_PROMPT" ] && [ "$T2_PROMPT" != "-" ]; then
                    T2_PROMPT_EPOCH=$(parse_iso_to_epoch "$T2_PROMPT")
                    R_PROMPT_ISO="$T2_PROMPT"
                    TIER2_RECOVERED=true
                    if [ -n "$R_LAST_ISO" ] && [ "$R_LAST_ISO" != "-" ]; then
                        LAST_ACT_EPOCH=$(parse_iso_to_epoch "$R_LAST_ISO")
                        if [ -n "$LAST_ACT_EPOCH" ] && [ -n "$T2_PROMPT_EPOCH" ] && [ "$T2_PROMPT_EPOCH" -gt "$LAST_ACT_EPOCH" ]; then
                            R_STATE="ACTIVE"
                            R_END_ISO="-"
                        fi
                    fi
                    # Only update R_STATE from Tier-2 if R_STATE from the window is UNKNOWN.
                    # If window established a terminal state (DONE/INTERRUPTED) or ACTIVE, never overwrite it!
                    if [ "$R_STATE" = "UNKNOWN" ]; then
                        if [ -n "$T2_STATE" ] && [ "$T2_STATE" != "-" ]; then
                            R_STATE="$T2_STATE"
                        fi
                        if [ -n "$T2_END" ] && [ "$T2_END" != "-" ]; then
                            R_END_ISO="$T2_END"
                        fi
                    fi
                fi
            fi
        fi
    fi

    # Guard: if window state was DONE, never allow R_STATE to flip to ACTIVE
    if [ "$WINDOW_WAS_DONE" = true ]; then
        R_STATE="DONE"
    fi

    # Normalize state: never falsely promote UNKNOWN to ACTIVE unless recovered via Tier-2
    if [ "$R_PROMPT_ISO" = "-" ]; then
        R_STATE="NONE"
    elif [ "$R_STATE" = "UNKNOWN" ]; then
        R_STATE="NONE"
    fi

    PROMPT_EPOCH=""
    if [ "$R_PROMPT_ISO" != "-" ]; then
        PROMPT_EPOCH=$(parse_iso_to_epoch "$R_PROMPT_ISO")
    fi
    [ -z "$PROMPT_EPOCH" ] && R_STATE="NONE"
    if [ "$SUPPRESS_PENDING" = true ] && [ -z "$SESSION_EPOCH" ] && [ "$R_STATE" = "NONE" ]; then
        exit 0
    fi

    # Compute LAST_ACTIVITY:
    # When DONE: last activity is the last recorded event's epoch (or END_EPOCH)
    # When ACTIVE: last activity is max(last recorded event, transcript mtime)
    LAST_ACTIVITY=""
    LAST_EVENT_EPOCH=""
    if [ "$R_LAST_ISO" != "-" ]; then
        LAST_EVENT_EPOCH=$(parse_iso_to_epoch "$R_LAST_ISO")
    fi
    MTIME=$(get_file_mtime "$TRANSCRIPT")
    [ -n "$MTIME" ] && [ "$MTIME" -gt "$NOW" ] && MTIME="$NOW"
    [ -n "$LAST_EVENT_EPOCH" ] && [ "$LAST_EVENT_EPOCH" -gt "$NOW" ] && LAST_EVENT_EPOCH="$NOW"

    if [ "$R_STATE" = "DONE" ] || [ "$R_STATE" = "INTERRUPTED" ]; then
        if [ -n "$LAST_EVENT_EPOCH" ]; then
            LAST_ACTIVITY="$LAST_EVENT_EPOCH"
        elif [ -n "$R_END_ISO" ] && [ "$R_END_ISO" != "-" ]; then
            LAST_ACTIVITY=$(parse_iso_to_epoch "$R_END_ISO")
        fi
        [ -z "$LAST_ACTIVITY" ] && LAST_ACTIVITY="$MTIME"
    else
        if [ -n "$LAST_EVENT_EPOCH" ] && [ -n "$MTIME" ]; then
            LAST_ACTIVITY=$(( LAST_EVENT_EPOCH > MTIME ? LAST_EVENT_EPOCH : MTIME ))
        elif [ -n "$LAST_EVENT_EPOCH" ]; then
            LAST_ACTIVITY="$LAST_EVENT_EPOCH"
        elif [ -n "$MTIME" ]; then
            LAST_ACTIVITY="$MTIME"
        fi
    fi

    [ -z "$LAST_ACTIVITY" ] && [ -n "$PROMPT_EPOCH" ] && LAST_ACTIVITY="$PROMPT_EPOCH"
    [ -z "$LAST_ACTIVITY" ] && [ -n "$SESSION_EPOCH" ] && LAST_ACTIVITY="$SESSION_EPOCH"
    [ -z "$LAST_ACTIVITY" ] && LAST_ACTIVITY="$NOW"
    [ "$LAST_ACTIVITY" -gt "$NOW" ] && LAST_ACTIVITY="$NOW"

    # Classify state for active stall detection
    if [ "$R_STATE" = "ACTIVE" ]; then
        if (( NOW - LAST_ACTIVITY > AGY_STALL_SECS )); then
            R_STATE="STALLED"
        fi
    fi

    # Render Round Segment
    if [ "$R_STATE" = "DONE" ]; then
        END_EPOCH=""
        if [ "$R_END_ISO" != "-" ]; then
            END_EPOCH=$(parse_iso_to_epoch "$R_END_ISO")
        fi
        [ -z "$END_EPOCH" ] && END_EPOCH="$LAST_ACTIVITY"
        [ -n "$END_EPOCH" ] && [ "$END_EPOCH" -gt "$NOW" ] && END_EPOCH="$NOW"
        [ -n "$PROMPT_EPOCH" ] && [ "$PROMPT_EPOCH" -gt "$NOW" ] && PROMPT_EPOCH="$NOW"
        ROUND_SECS=$(( END_EPOCH - PROMPT_EPOCH ))
        [ $ROUND_SECS -lt 0 ] && ROUND_SECS=0
        ROUND_DUR=$(format_duration "$ROUND_SECS")
        ROUND_TEXT="${GREEN}󰔛 ${ROUND_DUR}${RESET}"
        RAW_ROUND="󰔛 ${ROUND_DUR}"
    elif [ "$R_STATE" = "INTERRUPTED" ]; then
        END_EPOCH=""
        if [ "$R_END_ISO" != "-" ]; then
            END_EPOCH=$(parse_iso_to_epoch "$R_END_ISO")
        fi
        [ -z "$END_EPOCH" ] && END_EPOCH="$LAST_ACTIVITY"
        [ -n "$END_EPOCH" ] && [ "$END_EPOCH" -gt "$NOW" ] && END_EPOCH="$NOW"
        [ -n "$PROMPT_EPOCH" ] && [ "$PROMPT_EPOCH" -gt "$NOW" ] && PROMPT_EPOCH="$NOW"
        ROUND_SECS=$(( END_EPOCH - PROMPT_EPOCH ))
        [ $ROUND_SECS -lt 0 ] && ROUND_SECS=0
        ROUND_DUR=$(format_duration "$ROUND_SECS")
        ROUND_TEXT="${RED}󰔛 ${ROUND_DUR}${RESET}"
        RAW_ROUND="󰔛 ${ROUND_DUR}"
    elif [ "$R_STATE" = "STALLED" ]; then
        ROUND_SECS=$(( LAST_ACTIVITY - PROMPT_EPOCH ))
        [ $ROUND_SECS -lt 0 ] && ROUND_SECS=0
        ROUND_DUR=$(format_duration "$ROUND_SECS")
        ROUND_TEXT="${GRAY}󰏤 ${ROUND_DUR}${RESET}"
        RAW_ROUND="󰏤 ${ROUND_DUR}"
    elif [ "$R_STATE" = "ACTIVE" ]; then
        ROUND_SECS=$(( NOW - PROMPT_EPOCH ))
        [ $ROUND_SECS -lt 0 ] && ROUND_SECS=0
        ROUND_DUR=$(format_duration "$ROUND_SECS")
        ROUND_TEXT="${YELLOW}󱐋 ${ROUND_DUR}${RESET}"
        RAW_ROUND="󱐋 ${ROUND_DUR}"
    else
        ROUND_TEXT="${GRAY}󰔛 --${RESET}"
        RAW_ROUND="󰔛 --"
    fi

    # Render Session Segment
    SESSION_TEXT="${GRAY}󱎫 --:--${RESET}"
    RAW_SESSION="󱎫 --:--"

    if [ -n "$SESSION_EPOCH" ] && [[ "$SESSION_EPOCH" =~ ^[1-9][0-9]*$ ]]; then
        SESSION_SECS=$(( NOW - SESSION_EPOCH ))
        [ $SESSION_SECS -lt 0 ] && SESSION_SECS=0
        SESSION_DUR=$(format_duration "$SESSION_SECS")
        if [ "$R_STATE" = "ACTIVE" ] || [ "$R_STATE" = "DONE" ]; then
            SESSION_TEXT="${CYAN}󱎫 ${SESSION_DUR}${RESET}"
        else
            SESSION_TEXT="${GRAY}󱎫 ${SESSION_DUR}${RESET}"
        fi
        RAW_SESSION="󱎫 ${SESSION_DUR}"
    fi
fi

# =============================================================================
# ASSEMBLE TIMER SEGMENTS
# =============================================================================
SEP=" ${GRAY}│${RESET} "
RAW_SEP=" │ "

CONTENT="${SESSION_TEXT}${SEP}${ROUND_TEXT}"
RAW_CONTENT="${RAW_SESSION}${RAW_SEP}${RAW_ROUND}"

# If segment mode requested, output formatted content and raw text separated by tab
if [ "$SEGMENT_MODE" = true ]; then
    printf "%b\t%s\n" "$CONTENT" "$RAW_CONTENT"
    exit 0
fi

# =============================================================================
# STANDALONE BORDER LINE
# =============================================================================
WIDTH=""
if [ -n "$INPUT_JSON" ]; then
    WIDTH=$(echo "$INPUT_JSON" | jq -r '.terminal.width // .columns // empty' 2>/dev/null)
fi
[ -z "$WIDTH" ] && [ -n "$STATUS_BAR_WIDTH" ] && WIDTH="$STATUS_BAR_WIDTH"
if [ -z "$WIDTH" ]; then
    if { true >/dev/null 2>&1 < /dev/tty; }; then
        WIDTH=$(stty size 2>/dev/null < /dev/tty | awk '{print $2}')
    fi
fi
[ -z "$WIDTH" ] && WIDTH=$(tput cols 2>/dev/null)
[ -z "$WIDTH" ] && WIDTH="${COLUMNS:-160}"
[[ "$WIDTH" =~ ^[0-9]+$ ]] || WIDTH=160
[ "$WIDTH" -lt 20 ] && WIDTH=20
[ "$WIDTH" -gt 1000 ] && WIDTH=1000

# Overhead: "───[ " (5) + " ]" (2) = 7 chars
RAW_TOTAL=$(( 7 + ${#RAW_CONTENT} ))

FILL_LEN=$(( WIDTH - RAW_TOTAL ))
[ "$FILL_LEN" -lt 0 ] && FILL_LEN=0

LINE_FILL=""
if [ "$FILL_LEN" -gt 0 ]; then
    printf -v LINE_FILL '%*s' "$FILL_LEN" ''
    LINE_FILL="${LINE_FILL// /─}"
fi

echo -e "${GRAY}───[ ${RESET}${CONTENT}${GRAY} ]${LINE_FILL}${RESET}"
