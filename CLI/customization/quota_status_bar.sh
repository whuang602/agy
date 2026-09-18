#!/usr/bin/env bash
#
# AGY Custom Status Line - Consumer Account Quota
#
# Extracts dual-window (5-hour and weekly) request quota and reset countdown from AGY CLI stdin JSON.
# Model-aware bucket selection (Gemini vs. 3rd-party models).
# Gracefully suppresses output (clean exit 0) for enterprise accounts, API key
# sessions, or whenever quota information is missing, empty, or disabled.
#

# --- Colors ---
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
GRAY="\033[90m"
RESET="\033[0m"

# --- Parse Arguments ---
SEGMENT_MODE=false
FORMAT_STYLE="compact"  # "compact" (󱓞 91.5% (5h) · 80.8% (wk) │ 󰔟 1h 59m) or "verbose"

while [ $# -gt 0 ]; do
    case "$1" in
        --segment)
            SEGMENT_MODE=true
            shift
            ;;
        --verbose)
            FORMAT_STYLE="verbose"
            shift
            ;;
        --compact)
            FORMAT_STYLE="compact"
            shift
            ;;
        -h|--help)
            cat << 'EOF'
Usage: quota_status_bar.sh [OPTIONS]

AGY Custom Status Line - Consumer Account Quota (Dual-Window & Single-Bucket)

Options:
  --segment   Output segment format: <COLORED>\t<RAW>
  --compact   Compact glyph format (e.g. 󱓞 91.5% (5h) · 80.8% (wk) │ 󰔟 1h 59m) [default]
  --verbose   Verbose text format (e.g. quota: 91.5% (5h) · 80.8% (wk) │ resets in 1h 59m)
  -h, --help  Show this help message
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

# Cleanly suppress if no stdin input received
[ -z "$INPUT_JSON" ] && exit 0

# --- Helper: Parse ISO-8601 UTC to Unix Epoch safely ---
parse_iso_to_epoch() {
    local iso="$1"
    [ -z "$iso" ] && return 1
    local epoch
    # GNU date (force UTC)
    epoch=$(date -u -d "$iso" +%s 2>/dev/null)
    if [ -n "$epoch" ]; then
        echo "$epoch"
        return 0
    fi
    # BSD date (macOS, force UTC)
    epoch=$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null)
    if [ -n "$epoch" ]; then
        echo "$epoch"
        return 0
    fi
    # Python3 fallback (argv passing to avoid code injection)
    if command -v python3 >/dev/null 2>&1; then
        epoch=$(python3 -c "import sys, datetime; print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z', '+00:00')).timestamp()))" "$iso" 2>/dev/null)
        if [ -n "$epoch" ]; then
            echo "$epoch"
            return 0
        fi
    fi
    return 1
}

## --- Helper: Format countdown duration ---
format_countdown() {
    local secs="$1"
    if [ -z "$secs" ] || ! [[ "$secs" =~ ^[0-9]+$ ]] || [ "$secs" -le 0 ]; then
        return 0
    fi
    [ "$secs" -gt 1209600 ] && secs=1209600

    local d=$(( secs / 86400 ))
    local rem=$(( secs % 86400 ))
    local h=$(( rem / 3600 ))
    local m=$(( (rem % 3600) / 60 ))

    if [ "$d" -gt 0 ]; then
        if [ "$h" -gt 0 ]; then
            echo "${d}d ${h}h"
        else
            echo "${d}d"
        fi
    elif [ "$h" -gt 0 ] && [ "$m" -gt 0 ]; then
        echo "${h}h ${m}m"
    elif [ "$h" -gt 0 ]; then
        echo "${h}h"
    elif [ "$m" -gt 0 ]; then
        echo "${m}m"
    else
        echo "<1m"
    fi
}

# --- Extract Quota Metrics via JQ ---
PARSED_DATA=$(echo "$INPUT_JSON" | jq -r '
def is_bucket:
  type == "object" and
  (.remaining_fraction != null or
   .remaining_percentage != null or
   .remaining_amount != null or
   .remaining_fca_quota != null or
   .used_percentage != null or
   .used_amount != null);

def is_enabled_bucket:
  is_bucket and (.disabled != true);

def derive_pct:
  if . == null then null
  elif (.remaining_percentage != null and (.remaining_percentage | type == "number")) then
    .remaining_percentage
  elif (.remaining_fraction != null and (.remaining_fraction | type == "number")) then
    (.remaining_fraction * 100)
  elif (.used_percentage != null and (.used_percentage | type == "number")) then
    (100 - .used_percentage)
  elif (.used_amount != null and .quota_value != null and ((.quota_value | tonumber? // 0) > 0)) then
    ((((.quota_value | tonumber) - (.used_amount | tonumber? // 0)) / (.quota_value | tonumber)) * 100)
  elif (.used_amount != null and .limit != null and ((.limit | tonumber? // 0) > 0)) then
    ((((.limit | tonumber) - (.used_amount | tonumber? // 0)) / (.limit | tonumber)) * 100)
  elif (.remaining_fca_quota != null and .quota_value != null and ((.quota_value | tonumber? // 0) > 0)) then
    (((.remaining_fca_quota | tonumber? // 0) / (.quota_value | tonumber)) * 100)
  elif (.remaining_amount != null and .quota_value != null and ((.quota_value | tonumber? // 0) > 0)) then
    (((.remaining_amount | tonumber? // 0) / (.quota_value | tonumber)) * 100)
  elif (.remaining_amount != null and .limit != null and ((.limit | tonumber? // 0) > 0)) then
    (((.remaining_amount | tonumber? // 0) / (.limit | tonumber)) * 100)
  else
    null
  end |
  if . == null then null elif . < 0 then 0 elif . > 100 then 100 else . end;

def format_pct:
  (. * 10 | round) as $t |
  if ($t % 10) == 0 then "\($t / 10 | floor)%" else "\($t / 10 | floor).\($t % 10)%" end;

def get_color:
  ((. * 10 | round) / 10) as $rounded |
  if $rounded >= 50 then "green"
  elif $rounded >= 20 then "yellow"
  else "red"
  end;

def is_consumed:
  ((derive_pct // 100) * 10 | round / 10) < 100;

((.quota? // .statusLine?.quota? // empty)) as $raw_quota |
if ($raw_quota == null or $raw_quota == {} or ($raw_quota | type != "object")) then
  empty
elif ($raw_quota | is_bucket) then
  if ($raw_quota | is_enabled_bucket) then
    ($raw_quota | derive_pct) as $pct |
    if $pct == null then empty else
      (($pct * 10 | round) / 10) as $shown |
      ["single", ($pct | format_pct), ($pct | get_color), ($shown < 100), ($raw_quota.reset_in_seconds // ""), ($raw_quota.reset_time // ""), ""] | @tsv
    end
  else empty end
else
  ($raw_quota | to_entries | map(select(.value | is_enabled_bucket))) as $entries |
  if ($entries | length) == 0 then
    empty
  elif ($entries | length) == 1 then
    ($entries[0].value) as $b |
    ($b | derive_pct) as $pct |
    if $pct == null then empty else
      (($pct * 10 | round) / 10) as $shown |
      (if ($entries[0].key | test("5h|5[-_]?hour"; "i")) then "5h"
       elif ($entries[0].key | test("weekly|week"; "i")) then "wk"
       else "" end) as $lbl |
      ["single", ($pct | format_pct), ($pct | get_color), ($shown < 100), ($b.reset_in_seconds // ""), ($b.reset_time // ""), $lbl] | @tsv
    end
  else
    (( (.model? | if type=="object" then (.id? // .display_name?) elif type=="string" then . else null end) // "") | tostring | ascii_downcase) as $model_str |
    (if ($model_str | test("claude|gpt|anthropic|openai|^3p|\\b3p\\b")) then "3p"
     elif ($model_str | test("gemini")) then "gemini"
     else null end) as $matched_group |
    
    (if $matched_group != null and ($entries | any(.key | test("^" + $matched_group + "[-_]"; "i"))) then
       $matched_group
     elif ($entries | any((.key | test("^gemini[-_]"; "i")) and (.value | is_consumed))) then
       "gemini"
     elif ($entries | any((.key | test("^3p[-_]"; "i")) and (.value | is_consumed))) then
       "3p"
     elif ($entries | any(.key | test("^gemini[-_]"; "i"))) then
       "gemini"
     elif ($entries | any(.key | test("^3p[-_]"; "i"))) then
       "3p"
     else
       null
     end) as $group |
    
    (if $group != null then
       ($entries | map(select(.key | test("^" + $group + "[-_]?(5h|5[-_]?hour)$"; "i"))) | .[0].value // null) as $b5h |
       ($entries | map(select(.key | test("^" + $group + "[-_]?(weekly|week)$"; "i"))) | .[0].value // null) as $bwk |
       if ($b5h != null and $bwk != null) then
         ($b5h | derive_pct) as $p5h |
         ($bwk | derive_pct) as $pwk |
         if ($p5h != null and $pwk != null) then
           (($p5h * 10 | round) / 10) as $shown5h |
           ["dual", ($p5h | format_pct), ($p5h | get_color), ($shown5h < 100), ($b5h.reset_in_seconds // ""), ($b5h.reset_time // ""), ($pwk | format_pct), ($pwk | get_color)] | @tsv
         elif $p5h != null then
           (($p5h * 10 | round) / 10) as $shown |
           ["single", ($p5h | format_pct), ($p5h | get_color), ($shown < 100), ($b5h.reset_in_seconds // ""), ($b5h.reset_time // ""), "5h"] | @tsv
         elif $pwk != null then
           (($pwk * 10 | round) / 10) as $shown |
           ["single", ($pwk | format_pct), ($pwk | get_color), ($shown < 100), ($bwk.reset_in_seconds // ""), ($bwk.reset_time // ""), "wk"] | @tsv
         else
           empty
         end
       elif $b5h != null then
         ($b5h | derive_pct) as $p5h |
         if $p5h != null then
           (($p5h * 10 | round) / 10) as $shown |
           ["single", ($p5h | format_pct), ($p5h | get_color), ($shown < 100), ($b5h.reset_in_seconds // ""), ($b5h.reset_time // ""), "5h"] | @tsv
         else empty end
       elif $bwk != null then
         ($bwk | derive_pct) as $pwk |
         if $pwk != null then
           (($pwk * 10 | round) / 10) as $shown |
           ["single", ($pwk | format_pct), ($pwk | get_color), ($shown < 100), ($bwk.reset_in_seconds // ""), ($bwk.reset_time // ""), "wk"] | @tsv
         else empty end
       else
         null
       end
     else
       null
     end) as $dual_result |
    
    if $dual_result != null then
      $dual_result
    else
      # Generic fallback without group prefix
      ($entries | map(select(.key | test("^(5h|5[-_]?hour)$"; "i"))) | .[0].value // null) as $b5h |
      ($entries | map(select(.key | test("^(weekly|week)$"; "i"))) | .[0].value // null) as $bwk |
      if ($b5h != null and $bwk != null) then
        ($b5h | derive_pct) as $p5h |
        ($bwk | derive_pct) as $pwk |
        if ($p5h != null and $pwk != null) then
          (($p5h * 10 | round) / 10) as $shown5h |
          ["dual", ($p5h | format_pct), ($p5h | get_color), ($shown5h < 100), ($b5h.reset_in_seconds // ""), ($b5h.reset_time // ""), ($pwk | format_pct), ($pwk | get_color)] | @tsv
        elif $p5h != null then
          (($p5h * 10 | round) / 10) as $shown |
          ["single", ($p5h | format_pct), ($p5h | get_color), ($shown < 100), ($b5h.reset_in_seconds // ""), ($b5h.reset_time // ""), "5h"] | @tsv
        elif $pwk != null then
          (($pwk * 10 | round) / 10) as $shown |
          ["single", ($pwk | format_pct), ($pwk | get_color), ($shown < 100), ($bwk.reset_in_seconds // ""), ($bwk.reset_time // ""), "wk"] | @tsv
        else
          empty
        end
      elif $b5h != null then
        ($b5h | derive_pct) as $p5h |
        if $p5h != null then
          (($p5h * 10 | round) / 10) as $shown |
          ["single", ($p5h | format_pct), ($p5h | get_color), ($shown < 100), ($b5h.reset_in_seconds // ""), ($b5h.reset_time // ""), "5h"] | @tsv
        else empty end
      elif $bwk != null then
        ($bwk | derive_pct) as $pwk |
        if $pwk != null then
          (($pwk * 10 | round) / 10) as $shown |
          ["single", ($pwk | format_pct), ($pwk | get_color), ($shown < 100), ($bwk.reset_in_seconds // ""), ($bwk.reset_time // ""), "wk"] | @tsv
        else empty end
      else
        ($entries[0].value) as $b |
        ($b | derive_pct) as $pct |
        if $pct != null then
          (($pct * 10 | round) / 10) as $shown |
          (if ($entries[0].key | test("5h|5[-_]?hour"; "i")) then "5h"
           elif ($entries[0].key | test("weekly|week"; "i")) then "wk"
           else "" end) as $lbl |
          ["single", ($pct | format_pct), ($pct | get_color), ($shown < 100), ($b.reset_in_seconds // ""), ($b.reset_time // ""), $lbl] | @tsv
        else empty end
      end
    end
  end
end
' 2>/dev/null)

[ -z "$PARSED_DATA" ] && exit 0

MODE=$(echo "$PARSED_DATA" | cut -f1)
[ -z "$MODE" ] && exit 0

case "$MODE" in
    dual)
        PCT_5H_STR=$(echo "$PARSED_DATA" | cut -f2)
        COLOR_5H=$(echo "$PARSED_DATA" | cut -f3)
        ACTIVE_5H=$(echo "$PARSED_DATA" | cut -f4)
        RESET_SECS_5H=$(echo "$PARSED_DATA" | cut -f5)
        RESET_TIME_5H=$(echo "$PARSED_DATA" | cut -f6)
        PCT_WK_STR=$(echo "$PARSED_DATA" | cut -f7)
        COLOR_WK=$(echo "$PARSED_DATA" | cut -f8)

        case "$COLOR_5H" in
            green)  C_5H="$GREEN" ;;
            yellow) C_5H="$YELLOW" ;;
            red)    C_5H="$RED" ;;
            *)      C_5H="$GREEN" ;;
        esac

        case "$COLOR_WK" in
            green)  C_WK="$GREEN" ;;
            yellow) C_WK="$YELLOW" ;;
            red)    C_WK="$RED" ;;
            *)      C_WK="$GREEN" ;;
        esac

        COUNTDOWN=""
        if [ "$ACTIVE_5H" = "true" ]; then
            SECS=""
            if [[ "$RESET_SECS_5H" =~ ^[0-9]+$ ]] && [ "$RESET_SECS_5H" -gt 0 ]; then
                SECS="$RESET_SECS_5H"
            elif [ -n "$RESET_TIME_5H" ]; then
                RESET_EPOCH=$(parse_iso_to_epoch "$RESET_TIME_5H")
                if [[ "$RESET_EPOCH" =~ ^[0-9]+$ ]]; then
                    NOW=$(date +%s)
                    DIFF=$(( RESET_EPOCH - NOW ))
                    if [ "$DIFF" -gt 0 ]; then
                        SECS="$DIFF"
                    fi
                fi
            fi
            if [ -n "$SECS" ] && [ "$SECS" -gt 0 ]; then
                COUNTDOWN=$(format_countdown "$SECS")
            fi
        fi

        DOT=" ${GRAY}·${RESET} "
        RAW_DOT=" · "
        SEP=" ${GRAY}│${RESET} "
        RAW_SEP=" │ "

        if [ "$FORMAT_STYLE" = "verbose" ]; then
            SEG_5H="${C_5H}quota: ${PCT_5H_STR} (5h)${RESET}"
            RAW_5H="quota: ${PCT_5H_STR} (5h)"
            SEG_WK="${C_WK}${PCT_WK_STR} (wk)${RESET}"
            RAW_WK="${PCT_WK_STR} (wk)"
            CONTENT_NO_COUNTDOWN="${SEG_5H}${DOT}${SEG_WK}"
            RAW_NO_COUNTDOWN="${RAW_5H}${RAW_DOT}${RAW_WK}"
            CONTENT_5H="${SEG_5H}"
            RAW_5H="${RAW_5H}"
            if [ -n "$COUNTDOWN" ]; then
                SEG_RESET="${GRAY}resets in ${COUNTDOWN}${RESET}"
                RAW_RESET="resets in ${COUNTDOWN}"
                CONTENT="${CONTENT_NO_COUNTDOWN}${SEP}${SEG_RESET}"
                RAW_CONTENT="${RAW_NO_COUNTDOWN}${RAW_SEP}${RAW_RESET}"
            else
                CONTENT="${CONTENT_NO_COUNTDOWN}"
                RAW_CONTENT="${RAW_NO_COUNTDOWN}"
            fi
        else
            # compact
            SEG_5H="${C_5H}󱓞 ${PCT_5H_STR} (5h)${RESET}"
            RAW_5H="󱓞 ${PCT_5H_STR} (5h)"
            SEG_WK="${C_WK}${PCT_WK_STR} (wk)${RESET}"
            RAW_WK="${PCT_WK_STR} (wk)"
            CONTENT_NO_COUNTDOWN="${SEG_5H}${DOT}${SEG_WK}"
            RAW_NO_COUNTDOWN="${RAW_5H}${RAW_DOT}${RAW_WK}"
            CONTENT_5H="${SEG_5H}"
            RAW_5H="${RAW_5H}"
            if [ -n "$COUNTDOWN" ]; then
                SEG_RESET="${GRAY}󰔟 ${COUNTDOWN}${RESET}"
                RAW_RESET="󰔟 ${COUNTDOWN}"
                CONTENT="${SEG_5H}${DOT}${SEG_WK}${SEP}${SEG_RESET}"
                RAW_CONTENT="${RAW_5H}${RAW_DOT}${RAW_WK}${RAW_SEP}${RAW_RESET}"
            else
                CONTENT="${SEG_5H}${DOT}${SEG_WK}"
                RAW_CONTENT="${RAW_5H}${RAW_DOT}${RAW_WK}"
            fi
        fi
        ;;

    single)
        PCT_STR=$(echo "$PARSED_DATA" | cut -f2)
        COLOR=$(echo "$PARSED_DATA" | cut -f3)
        ACTIVE=$(echo "$PARSED_DATA" | cut -f4)
        RESET_SECS=$(echo "$PARSED_DATA" | cut -f5)
        RESET_TIME=$(echo "$PARSED_DATA" | cut -f6)
        WINDOW_LBL=$(echo "$PARSED_DATA" | cut -f7)

        case "$COLOR" in
            green)  C_SINGLE="$GREEN" ;;
            yellow) C_SINGLE="$YELLOW" ;;
            red)    C_SINGLE="$RED" ;;
            *)      C_SINGLE="$GREEN" ;;
        esac

        COUNTDOWN=""
        if [ "$ACTIVE" = "true" ]; then
            SECS=""
            if [[ "$RESET_SECS" =~ ^[0-9]+$ ]] && [ "$RESET_SECS" -gt 0 ]; then
                SECS="$RESET_SECS"
            elif [ -n "$RESET_TIME" ]; then
                RESET_EPOCH=$(parse_iso_to_epoch "$RESET_TIME")
                if [[ "$RESET_EPOCH" =~ ^[0-9]+$ ]]; then
                    NOW=$(date +%s)
                    DIFF=$(( RESET_EPOCH - NOW ))
                    if [ "$DIFF" -gt 0 ]; then
                        SECS="$DIFF"
                    fi
                fi
            fi
            if [ -n "$SECS" ] && [ "$SECS" -gt 0 ]; then
                COUNTDOWN=$(format_countdown "$SECS")
            fi
        fi

        SEP=" ${GRAY}│${RESET} "
        RAW_SEP=" │ "

        LBL_SUFFIX=""
        RAW_LBL_SUFFIX=""
        if [ -n "$WINDOW_LBL" ]; then
            LBL_SUFFIX=" (${WINDOW_LBL})"
            RAW_LBL_SUFFIX=" (${WINDOW_LBL})"
        fi

        if [ "$FORMAT_STYLE" = "verbose" ]; then
            SEG_PCT="${C_SINGLE}quota: ${PCT_STR}${LBL_SUFFIX}${RESET}"
            RAW_PCT="quota: ${PCT_STR}${RAW_LBL_SUFFIX}"
            CONTENT_NO_COUNTDOWN="${SEG_PCT}"
            RAW_NO_COUNTDOWN="${RAW_PCT}"
            CONTENT_5H="${SEG_PCT}"
            RAW_5H="${RAW_PCT}"
            if [ -n "$COUNTDOWN" ]; then
                SEG_RESET="${GRAY}resets in ${COUNTDOWN}${RESET}"
                RAW_RESET="resets in ${COUNTDOWN}"
                CONTENT="${SEG_PCT}${SEP}${SEG_RESET}"
                RAW_CONTENT="${RAW_PCT}${RAW_SEP}${RAW_RESET}"
            else
                CONTENT="${SEG_PCT}"
                RAW_CONTENT="${RAW_PCT}"
            fi
        else
            # compact
            SEG_PCT="${C_SINGLE}󱓞 ${PCT_STR}${LBL_SUFFIX}${RESET}"
            RAW_PCT="󱓞 ${PCT_STR}${RAW_LBL_SUFFIX}"
            CONTENT_NO_COUNTDOWN="${SEG_PCT}"
            RAW_NO_COUNTDOWN="${RAW_PCT}"
            CONTENT_5H="${SEG_PCT}"
            RAW_5H="${RAW_PCT}"
            if [ -n "$COUNTDOWN" ]; then
                SEG_RESET="${GRAY}󰔟 ${COUNTDOWN}${RESET}"
                RAW_RESET="󰔟 ${COUNTDOWN}"
                CONTENT="${SEG_PCT}${SEP}${SEG_RESET}"
                RAW_CONTENT="${RAW_PCT}${RAW_SEP}${RAW_RESET}"
            else
                CONTENT="${SEG_PCT}"
                RAW_CONTENT="${RAW_PCT}"
            fi
        fi
        ;;

    *)
        exit 0
        ;;
esac

# If segment mode requested, output formatted content and raw text separated by tab
if [ "$SEGMENT_MODE" = true ]; then
    printf "%b\t%s\t%b\t%s\t%b\t%s\n" "$CONTENT" "$RAW_CONTENT" "$CONTENT_NO_COUNTDOWN" "$RAW_NO_COUNTDOWN" "$CONTENT_5H" "$RAW_5H"
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
