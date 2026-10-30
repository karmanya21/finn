# Daily filings refresh from India

BSE's announcements API refuses GitHub's runners (403), so the filings digest is refreshed
from a small server in India instead. Prices still refresh on GitHub (`.github/workflows/refresh.yml`).

## 1. Create the server (Oracle Cloud, Always Free)

1. Sign up at <https://signup.cloud.oracle.com>. **Pick Mumbai or Hyderabad as your home
   region.** It can't be changed later, and Always Free resources only live there.
2. Compute → Instances → **Create instance**
   - Image: **Ubuntu 24.04**
   - Shape: **VM.Standard.E2.1.Micro** (Always Free eligible)
   - SSH keys: **upload your Mac's public key** (`~/.ssh/id_ed25519.pub`; create one with
     `ssh-keygen -t ed25519` if you don't have one)
3. Note the instance's **public IP** once it's running.

## 2. Check, then set up (two commands)

```bash
ssh ubuntu@<public-ip>
git clone https://github.com/karmanya21/finn.git ~/finn && bash ~/finn/deploy/india-refresh/setup.sh
```

`setup.sh` installs Python, then **checks that BSE answers this server and stops if it
doesn't** (cloud addresses can be blocked even inside India). If BSE answers, it:

- creates a deploy key and asks you to add it at
  <https://github.com/karmanya21/finn/settings/keys/new> with **Allow write access**
  (this key can push to this one repository only);
- asks for your Gemini API key and stores it on the server only (`~/finn/.env`, mode 600);
- schedules `refresh.sh` daily at 19:15 IST and runs it once.

## Day to day

- Log: `tail -50 ~/finn-refresh.log`
- Run now: `~/finn/deploy/india-refresh/refresh.sh`
- If BSE fails for most companies, the previous digest is kept (never overwritten with an
  empty one) and the log says `STALE`.
- To stop it: `crontab -e` and delete the `india-refresh` line; remove the deploy key on GitHub.
