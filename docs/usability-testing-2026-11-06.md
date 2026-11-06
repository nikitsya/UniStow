# UniStow usability testing — 6 November 2026

A renter used unistow.app on a phone to open Locker 4, close it, collect their belongings and finish the rental. The
physical door opened, held shut after closing and the rental eventually showed **Completed**. The interface review
around that journey focused on three questions: Which locker can I use? What should I do next? How do I back out safely?

This record separates the renter’s observations from design changes and browser checks. The phone journey took place
during development. Later refinements were deployed through commit `7dd8e97`.

## Finding a locker and moving around

| What the renter noticed                                                                                                                                                            | What changed and why                                                                                                                                                                                                                                                           |
|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| On the phone, the available locker showed a blue arrow emoji. The desktop screenshot showed a thin diagonal arrow.                                                                 | Locker and rental links now use a drawn SVG arrow. It takes its colour from the link, so the phone cannot substitute a coloured emoji.                                                                                                                                         |
| In the nine-locker grid, the cards looked tall and narrow. A line below the grid added another visual boundary.                                                                    | Made the cards square and removed the divider. The locker number, size and daily price stay together. A pale green card labelled **Available** stands out among neutral cards labelled **Unavailable**. The words carry the meaning as well as the colours.                    |
| Switching to **My rentals** changed the header, removed the familiar account icon and offered a redundant **Back to lockers** link.                                                | Both list pages now share the branded header and bottom navigation. The renter can switch directly between **Lockers** and **My rentals** in either direction.                                                                                                                 |
| Booking repeated “Locker 4” in several places. Returning to the list made the change in header size and layout especially noticeable. The payment page had the same inconsistency. | Reused the home header throughout the site. Only the short line describing the page changes. Detail pages put a round back button where the account control sits on the list pages. The illustrated locker keeps its **04**, where the number helps identify the physical box. |
| The U looked off-centre in its frame.                                                                                                                                              | Centred the U and reused the same mark in the shared header and browser tab so the site is recognisable throughout the journey.                                                                                                                                                |

## Reserving, paying and changing your mind

- **Before payment.** Someone who changes their mind during the 15-minute reservation can use **Cancel reservation**
  below **Pay**. This releases the locker for someone else without waiting for the reservation to expire.
- **Confirming cancellation.** A styled dialogue asks **Cancel your reservation?** and explains who can use the locker
  afterwards. The white **Keep** button closes the dialogue. The pale orange **Cancel** button confirms cancellation.
  Short labels avoid repeating “reservation” three times. The main payment action stays dark green.
- **After payment.** The message above the buttons has the same breathing room as the pre-payment guidance. **Payment
  confirmed. Your rental is active.** appears once per rental in that browser tab. Opening and closing the door should
  not bring back an old payment announcement.
- **Reading feedback.** Opening, collection and payment updates occupy the message area above the relevant buttons. Each
  new instruction replaces the previous one. This keeps the next step in view instead of making the renter look below
  the controls for a second explanation.

## Opening the door, closing it and finishing

| Situation                                                              | User-facing decision or observed result                                                                                                                                                                                                                                                                     |
|------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| The renter taps **Open locker**.                                       | Show **Unlocking…**, then **Open the door once it unlocks.** The user needs to know when to pull the door, without having to interpret a command acknowledgement or motor movement.                                                                                                                         |
| The door is already open.                                              | Keep **Open locker** visible but disabled. **Your locker door is open.** explains why, including when the renter returns to the page after walking away.                                                                                                                                                    |
| The renter closes the door.                                            | The page briefly used to flash a “not ready” warning while the latch was moving. Allow time for relocking and show **Securing your locker…** during that transition. In the physical test the page showed **Closed** and the renter checked that the door held shut.                                        |
| A light closure triggers the magnetic sensor, but the door comes ajar. | The renter observed the lowered latch blocking the next attempt to shut the door. The controller was changed to retract the latch if the sensor reports an open door during or after closing, including at startup. A sensor reading and a door that physically holds shut are separate checks.             |
| The renter chooses **Finish rental**.                                  | Use pale orange for the action that ends the rental and ask for confirmation first. The white **Cancel** button dismisses the dialogue. Both this button and **Keep** in the reservation dialogue have no thick brown outline or underline. Keyboard focus uses heavier text.                               |
| The renter confirms and collects their belongings.                     | Keep **Take all your belongings, then close the door to finish your rental.** above the disabled actions throughout collection. It takes precedence over the payment confirmation and ordinary door-open message. The renter confirmed on the phone that opening and closing the door led to **Completed**. |

## Touch, dialogues and interrupted connections

| Test or observation                                              | Result and scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
|------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Pull the page past an edge on a phone.                           | The renter wanted the content to stay in place without revealing the background, but still expected a downward pull at the top to refresh. The shared page styles suppress overscroll. A deliberate downward pull of at least 80 CSS pixels refreshes on release. Six scripted gesture cases covered a valid pull, a short pull, sideways and upward movement, a pull starting partway down the page and a two-finger gesture.                                                                   |
| Open a confirmation and interact with the background.            | A Chrome check found that scrolling was locked while a modal was open. A tap inside kept it open. A tap on the backdrop dismissed it and the page retained its scroll position. Dismissing the dialogue does not confirm cancellation or collection. This behaviour is shared across the site.                                                                                                                                                                                                   |
| Open each dialogue and inspect its initially focused button.     | Chrome checks on **Keep** and **Cancel** found no outline, border width or underline. The published stylesheet was compared with the checked version.                                                                                                                                                                                                                                                                                                                                            |
| Leave the site open during a server restart or loss of internet. | The renter saw unsolicited red connection messages while simply viewing a page. Background updates now retry quietly on the locker list, rental list and rental detail. The last loaded content stays visible. Scripted checks exercised a connection failure and an HTTP 503 response. Background payment checks left the existing message alone. Locker controls can be disabled while their state cannot be verified. Feedback still appears if an action the renter actually requests fails. |

## How the physical journey was verified

1. **Read the door.** The Pi published a closed-door reading. The backend logged it and the renter saw **Closed** on the
   active rental page.
2. **Check delivery before moving the latch.** A ping received a matching pong in 0.22 seconds. Test clicks from the
   rental page received acknowledgements in the controller log. These checks established delivery. The separate physical
   test confirmed movement.
3. **Open from the public site.** After the old empty test rental was closed, the renter made a fresh rental, tapped
   **Open locker** on the phone and observed Locker 4 open. After closing it, they checked both the displayed state and
   the physical latch.
4. **Finish from the phone.** The renter tapped **Finish rental**, opened and closed the door and reported
   **Completed**. This checked the outcome beyond the initial button press.
5. **Keep the locker usable after the first hour.** Automatic renewal was enabled for the controller’s one-hour PubNub
   token. The setup reported **Automatic token renewal active on Locker 4**. An opening after the original token expired
   was not recorded.

## Verification notes and remaining checks

The saved development runs include 58 passing tests for token renewal, 60 for rental notices and 61 in the
payment-confirmation run. These totals are separate runs of overlapping suites. Earlier runs skipped database-dependent
cases, and one physical-command test run recorded 17 errors when the test process could not connect to local PostgreSQL.
The phone observations above come from the real locker, independently of those automated runs.

After the final deployment, the public home page referenced the updated assets and the published dialogue, payment and
live-update scripts and styles matched the local release. The following checks still need a recorded result on the final
version:

- On iPhone Safari, pull down from the top to refresh, then try a short sideways drag. Open each dialogue and try to
  scroll behind it before tapping the backdrop.
- With an unpaid test reservation, choose **Keep** first and confirm that payment is still available. Then choose
  **Cancel** and check that the released locker becomes available to another renter.
- With the locker empty, repeat the light-closure case. Check that the latch retracts when the door comes ajar and that
  the next full closure holds.
- With a valid paid rental, try opening Locker 4 more than an hour after the initial token was issued and record the
  result.
