# Step 19: the phone

**Built:** the dashboard is responsive (checked from 320 to 1600 px wide; touch targets are larger on touch screens; charts take taps for their details) and it is
installable: it ships a web app manifest and icon (`/manifest.webmanifest`, `/icon.svg`, served behind the same token), so "Add to Home Screen" gives it its own
window. You can watch jobs, see what needs you, answer a question (never a secret) and look at the agent's browser from the phone.

**Not built, and why (each needs a decision from you):**
1. **Reaching it from outside this machine.** The dashboard listens on 127.0.0.1 and refuses any Host name it does not know, on purpose. Two safe ways in:
   - *an SSH tunnel* from the phone (`ssh -L 8765:127.0.0.1:8765 you@this-machine`, then open `http://127.0.0.1:8765/?t=TOKEN` on the phone), or
   - *Tailscale*: `tailscale serve --bg 8765` gives an HTTPS address on your own tailnet; then start the dashboard with `--allow-host <machine>.<tailnet>.ts.net`
     (the harness reads it from `DASHBOARD_ALLOW_HOSTS`, see `.env.example`). HTTPS is also what lets a phone install the page as an app.
   Opening it to your LAN or the internet is not offered: the dashboard can answer questions and start runs, and the agent's browser is unauthenticated noVNC behind it.
2. **Push notifications** ("a question is waiting", "a run failed"). They need a push service (a third party sees the message). Say if you want one (ntfy, Pushover, email to yourself).
3. **A native app.** The installable page covers monitoring and answering; a store app adds nothing you need yet.
4. **The agent's browser through the dashboard's own port.** Today the Browser tab frames noVNC on 6080 (loopback), which a phone away from the machine cannot reach.
   A proxy inside the dashboard would fix that and also let a read-only dashboard really refuse keyboard and mouse input; it is a contained piece of work once you decide on (1).
