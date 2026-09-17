# Giada Secret Access Bot

Flow:
1. User opens the bot.
2. Bot verifies membership in the public access channel @Giadasecret.
3. If not a member, the bot shows Join + Verify.
4. After membership is verified, the user receives a personal deep link.
5. A referral is counted only when a new person arrives through that deep link, starts the bot, and is verified as a member of @Giadasecret.
6. Each referred person can then create their own referral link.
7. At 3 verified referrals, the inviter receives the private-channel invite link.

Required Render environment variables:
- BOT_TOKEN = the token from BotFather
- PRIVATE_INVITE_URL = the invite link to the private Giada channel
- PUBLIC_URL is optional; on Render the code uses the built-in RENDER_EXTERNAL_URL automatically, e.g. https://giada-secret-bot.onrender.com

Optional variables:
- PUBLIC_CHANNEL = @Giadasecret
- PUBLIC_CHANNEL_URL = https://t.me/Giadasecret
- BOT_USERNAME = GiadaSecretAccessBot
- WEBHOOK_PATH = telegram
- PORT = 10000 (Render provides this automatically)
- DB_PATH = referrals.db

Important:
- Never put BOT_TOKEN in GitHub.
- The bot must be an administrator of @Giadasecret so getChatMember can verify membership.
- The SQLite database on Render Free is ephemeral. It is suitable for testing, but referral data can be lost when the service restarts or spins down. Use persistent PostgreSQL for a production setup.

Render Start Command: `python bot.py`.
