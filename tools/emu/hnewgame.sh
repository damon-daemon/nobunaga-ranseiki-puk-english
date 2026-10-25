#!/bin/bash
# hnewgame.sh image.bin out.png : headless boot -> new game, scenario 1 (1534 Nobunaga's
# birth), the first castle the cursor can reach (Miki, Sakurado castle), default settings,
# tutorial skipped, through the opening event, the first council (tutorial advisor, help
# page, proposal) to the strategy map (~4 min). Then `pine.py save 1` for a map state.
# The daimyo-select cursor moves freely (D-pad hold times tuned for the lake start position).
# SHOTS=dir : also save a screenshot at each stage (dir/s1_menu.png ... ) to check the run.
# Needs an EMPTY headless memory card (build/pcsx2h/PCSX2/memcards): with a save on it the main menu
# cursor starts on Load Game and the run loads that save instead.
# COUNCIL_STATE=n : also save state slot n just before the council (goal window tests).
D=$(cd "$(dirname "$0")" && pwd); PY="$D/../../.venv/bin/python"
pad() { "$PY" "$D/vpad.py" hold "$1" "$2" >/dev/null; timeout 0.4 tail -f /dev/null; }
shot() { [ -n "$SHOTS" ] && "$D/hshot.sh" "$SHOTS/$1.png" >/dev/null; }
"$D/headless.sh" stop >/dev/null; timeout 2 tail -f /dev/null
"$D/headless.sh" "$1"
for i in $(seq 20); do [ -S "$XDG_RUNTIME_DIR/nb9-vpad.sock" ] && break; timeout 1 tail -f /dev/null; done
timeout 55 tail -f /dev/null
"$D/hpress.sh" /dev/null Return w w w Return w w w            # movie -> title -> main menu
shot s1_menu
"$D/hpress.sh" /dev/null X w w w X w w w                      # new game -> scenario 1 -> daimyo select
"$D/hpress.sh" /dev/null Right Right w Up w                   # cursor from the lake towards Sakurado
pad Right 90; pad Down 70; pad Right 40; pad Down 40
"$D/hpress.sh" /dev/null X w w w; shot s2_confirm             # castle -> "play as X?" (yes)
"$D/hpress.sh" /dev/null X w w w; shot s3_settings             # -> settings
"$D/hpress.sh" /dev/null A w w X w w w w w                    # Triangle = decide -> "start with these settings?" yes
"$D/hpress.sh" /dev/null X w w w Left w X w w w w w           # succession? no; start as the lord? yes
timeout 8 tail -f /dev/null; shot s4_tutorial                 # tutorial title page
"$D/hpress.sh" /dev/null Left w X w w w w w X w w w w         # End -> "end the tutorial?" dialog
"$D/hpress.sh" /dev/null X w w w w X w w w w w                # yes -> "the story begins" -> opening event
timeout 6 tail -f /dev/null; shot s5_event
for i in $(seq 7); do "$D/hpress.sh" /dev/null X; timeout 3 tail -f /dev/null; shot s5_event$i; done   # event dialogue, narration
timeout 12 tail -f /dev/null                                  # AI turns, castle scene
for i in $(seq 4); do "$D/hpress.sh" /dev/null X; timeout 3 tail -f /dev/null; shot s6_hall$i; done   # narration, advisor -> help page
"$D/hpress.sh" /dev/null Right w X w w w                      # close the help page
[ -n "$COUNCIL_STATE" ] && "$PY" "$D/pine.py" save "$COUNCIL_STATE"
# council: narration, 3 advisor lines, proposal, confirm, advisor, objective, corps panel -> map
for i in $(seq 17); do "$D/hpress.sh" /dev/null X; timeout 3 tail -f /dev/null; shot s7_council$i; done
timeout 3 tail -f /dev/null
"$D/hshot.sh" "$2"
