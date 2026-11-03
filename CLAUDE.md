# CLAUDE.md

Read **HANDOFF.md** first. It has the full picture: what this system is, the repo layout, what is built, what is open, and the standing rules.

Short version of the rules:

- Never ask for or repeat API keys. Manan adds repository secrets himself.
- Never change the email recipient default in `digest.py`.
- Scheduled GitHub Actions can run 5 to 60 minutes late. That is normal.
- The `gh` CLI must be signed in as `manangoesagain`, not `Manan-J-Shah`.
- Run the offline tests before committing: `python -m unittest discover -s tests`
