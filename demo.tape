# demo.tape — regenerate the askrepo demo GIF + social-preview still.
#
# Prereqs (run everything from the repo root):
#   brew install vhs
#   secrun env PROVIDER=openai .venv/bin/python -m askrepo index ..   # build the index once
# Then:
#   vhs demo.tape
#
# Outputs:
#   assets/demo.gif       — the animated README demo
#   assets/demo-card.png  — a still of the hero answer, sized 1280x640 for the
#                           GitHub social preview (Settings -> General -> Social preview)
#
# This runs the REAL tool against the REAL index — every answer and every
# (path:line) citation is genuine, not scripted. If an answer hasn't finished
# streaming before the Screenshot/clear, bump the Sleep just above it.
#
# To show the pricier-but-sharper Claude answers instead, change PROVIDER=openai
# to PROVIDER=claude in the alias below (the existing index still works — the
# query is embedded with whatever stack the index was built with).

Require secrun

Output assets/demo.gif

Set Shell zsh
Set FontSize 20
Set Width 1280
Set Height 640
Set Padding 26
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

# --- hidden setup: make `askrepo` read as the clean command the README documents ---
Hide
Type "alias askrepo='secrun env PROVIDER=openai .venv/bin/python -m askrepo'"
Enter
Type "clear"
Enter
Show

# --- hero: barge-in — this frame is captured as the social card ---
Type "askrepo ask 'what is barge-in?'"
Enter
Sleep 7s
Screenshot assets/demo-card.png
Sleep 2s
Type "clear"
Enter
Sleep 400ms

# --- the counterintuitive one: caching that costs *more* ---
Type "askrepo ask 'when can prompt caching actually increase my cost?'"
Enter
Sleep 7s
Sleep 2s
Type "clear"
Enter
Sleep 400ms

# --- agent vs workflow ---
Type "askrepo ask 'what makes an agent different from a workflow?'"
Enter
Sleep 7s
Sleep 3s
