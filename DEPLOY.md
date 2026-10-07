# Deploying (Render + MongoDB Atlas, both free tiers)

## 1. Database (MongoDB Atlas)
1. Create a free M0 cluster at https://www.mongodb.com/atlas
2. Database Access -> add a user (username + password).
3. Network Access -> add IP `0.0.0.0/0` (Render's IPs are not fixed).
4. Connect -> Drivers -> copy the connection string:
   `mongodb+srv://USER:PASS@cluster0.xxxxx.mongodb.net/`
   (URL-encode special characters in the password.)

## 2. Push to GitHub
```bash
cd event-management-system
git init && git add . && git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/event-management-system.git
git push -u origin main
```

## 3. Render
1. https://render.com -> New -> Blueprint (or Web Service) -> select the repo.
2. Blueprint uses `render.yaml` automatically. If making a manual Web Service:
   - Root directory: `backend`
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
3. Add env var `MONGO_URI` = your Atlas string, and `SECRET_KEY` = any long random string
   (the Blueprint generates `SECRET_KEY` for you automatically; set it yourself only for a manual Web Service).
   Keep `SECRET_KEY` stable: changing it logs everyone out.
4. Deploy, then open `https://<your-service>.onrender.com/health`
   -> should show `{"db": "mongodb", "status": "ok"}`.

Note: the free Render tier sleeps after inactivity; first request takes ~30s.
Without MONGO_URI the app falls back to in-memory mongomock and loses data
on every restart, so always set it in production.

## 4. (Recommended) Turn on emails: password reset + email verification

Until email is configured: password-reset links are only written to the server log
(Render dashboard -> your service -> Logs) and **email verification is switched off**
(new accounts are active immediately), because nobody could receive the link.
Once email is configured, **new accounts must confirm their email before they can log in.**
To send real emails (free, no domain needed):

1. Create a free account at https://www.brevo.com
2. Senders, Domains & Dedicated IPs -> Senders -> add the email address you want
   emails to come from, and click the verification link Brevo sends you.
3. SMTP & API -> API Keys -> generate a key.
4. In Render -> your service -> Environment, add:
   - `BREVO_API_KEY` = the key
   - `MAIL_FROM` = the sender address you verified
   - `APP_URL` = your site URL, e.g. `https://your-app.onrender.com` (used in the reset link)
5. Save (Render redeploys). Test by signing up with a real address, then "Forgot password?".

Existing accounts created before email was configured stay active (they are not locked out).

Brevo's free plan allows about 300 emails per day. (Render's free tier blocks
normal SMTP, which is why the app uses Brevo's HTTPS API instead.)

## Security behaviour built in
- Passwords: minimum 8 characters, stored hashed.
- Login: 5 wrong passwords for an account (or 40 from one IP) locks login for 15 minutes.
  A successful password reset lifts the lock.
- Reset links expire after 1 hour and work only once. The "forgot password"
  response is identical whether or not the account exists.
- Forgot-password: max 3 requests per hour per account, 10 per IP.
- Sign-up: max 100 per hour per IP.
- Email verification: confirmation links last 48 hours. Accounts never confirmed within 48 hours
  are released, so the real owner of that email can still sign up. A password reset also
  confirms the email. "Resend confirmation email" is limited to 3 per hour per account.
