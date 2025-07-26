# DeFi Yield Dashboard

A real-time DeFi yield tracking dashboard with Telegram bot integration.

## Features

- 📊 Real-time PENDLE yield tracking
- 💰 Hyperliquid funding rates monitoring  
- 🤖 Telegram bot with auto push notifications
- 🌐 Beautiful web dashboard
- 📱 Mobile responsive design

## Deployment

This project is designed to run on Render.com for free 24/7 operation.

### Environment Variables Required:
- `BOT_TOKEN`: Your Telegram bot token

### Endpoints:
- `/` - Main dashboard
- `/health` - Health check (for monitoring)
- `/webhook` - Telegram webhook
- `/api/yields` - JSON API

## Monitoring

The `/health` endpoint should be monitored every 14 minutes to prevent Render from sleeping the service.

## Telegram Commands

- `/start` - Subscribe to updates
- `/check` - View current data  
- `/stop` - Unsubscribe

Auto push notifications are sent every 5 minutes to all subscribers.
