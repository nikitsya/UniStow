# PayBox

PayBox is an IoT smart locker project for short-term storage. The idea is to let a user
rent a locker through a web app, pay using Stripe test payments and open it from their account.
A Raspberry Pi controls the latch and reports the door status.

## How it will work

1. Sign in and check locker availability, price and rental end time.
2. Reserve the locker and complete a test payment.
3. Open the locker and reopen it during the rental without paying again.
4. Collect belongings, confirm the locker is empty and close the door to finish.

The first version uses Locker 4 at EUR 1 per calendar day, with 1 day selected by default
and up to 4 days payable in advance. Days end at 18:00 in Ireland; starts at or after 18:00
count the next date as day one. EUR 1 becomes due at the unpaid deadline and each following
18:00 until collection and rental completion. Payments will be in test mode, with no real money
charged. The web pages should work on mobile and be easy to use
with clear labels, keyboard controls and readable status messages.

## Planned stack

| Part             | Technology                                             |
|------------------|--------------------------------------------------------|
| Web app          | Python, FastAPI, Jinja2, HTML, CSS and JavaScript      |
| Database         | PostgreSQL                                             |
| Device messaging | PubNub, subject to channel access control availability |
| Payments         | Stripe test environment                                |
| Hardware         | Raspberry Pi, servo latch and door contact sensor      |
| Hosting          | AWS, Nginx and Uvicorn, with a custom HTTPS domain     |

The hardware prototype will use a small box, breadboard, jumper wires and suitable power supplies.

## Architecture

```mermaid
flowchart LR
    Browser -->|HTTPS| App[FastAPI on AWS]
    App --> DB[(PostgreSQL)]
    App -->|Checkout| Stripe[Stripe test payments]
    Stripe -->|Verified webhook| App
    App <-->|Commands and status| PubNub
    PubNub <--> Pi[Raspberry Pi]
    Pi --> Latch[Servo latch]
    Sensor[Door sensor] --> Pi
```

PostgreSQL will store users, lockers, rentals, payments and device commands/events.
The server will check payments and rental ownership before sending an unlock command.

## Main rules

- Require login and check ownership before showing rental data or allowing access.
- Prevent two users from reserving the same locker. Verify payment webhooks and handle repeated events only once.
- Give commands unique IDs and expiry times so stale or repeated messages cannot reopen the locker.
- Show payment, command acknowledgement and actual door state separately. Flag offline devices and failed access for
  assistance.
- Do not release a locker just because the rental expires. Require confirmed collection and a closed door; unknown
  sensor state needs assistance.
- Keep secrets out of Git, hash passwords, protect sessions and forms, and restrict device channels and server/database
  access.
- Use encrypted connections and encrypted storage/backups. Card details stay with Stripe.

## Project files

- `backend/` - FastAPI server, database connection and SQL files.
- `frontend/` - HTML templates, CSS and JavaScript, served by FastAPI.
- `android/` - Android prototype.
- `docs/` - Implementation plan, presentation and design images.
- [Implementation plan](docs/implementation-plan.md) - Next development steps.
- [Database plan](docs/database.md) - Proposed tables, fields and relationships.
- [AI assistance log](AI_Chat_Log.md) - Brief record of AI help.
