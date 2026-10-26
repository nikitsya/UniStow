from gpiozero import Button, Servo
from gpiozero.pins.pigpio import PiGPIOFactory
from time import monotonic, sleep

# Pulse widths define the latch positions; they are not measured angles.
CLOSED_PULSE_US = 1500
OPEN_PULSE_US = 2250


def wait_for_door_to_close(door):
    # Require a full second of continuous contact before moving the latch.
    closed_since = None
    while True:
        if not door.is_pressed:
            closed_since = None
        elif closed_since is None:
            closed_since = monotonic()
        elif monotonic() - closed_since >= 1:
            return
        sleep(0.05)


def main():
    factory = PiGPIOFactory(host="localhost")
    try:
        # GPIO17: physical pin 11. GPIO18: physical pin 12.
        with Button(17, pull_up=True, bounce_time=0.1, pin_factory=factory) as door, \
                Servo(18, initial_value=None,
                      min_pulse_width=CLOSED_PULSE_US / 1_000_000,
                      max_pulse_width=OPEN_PULSE_US / 1_000_000,
                      pin_factory=factory) as servo:
            sleep(0.2)
            if not door.is_pressed:
                print("Close the door, then run the test again. No movement commanded.")
                return

            wait_for_door_to_close(door)
            servo.min()
            sleep(1)
            print("Latch commanded closed (1500 us).")

            while True:
                command = input("Enter open or exit: ").strip().lower()
                if command == "exit":
                    break
                if command != "open":
                    print("Use open or exit.")
                    continue
                if not door.is_pressed:
                    print("Close the door before starting a new cycle.")
                    continue

                servo.max()
                print("Latch commanded open (2250 us). Open the door.")

                # A closed door alone must not trigger immediate relocking.
                while door.is_pressed:
                    sleep(0.05)
                print("Door opened. Close it when ready.")

                wait_for_door_to_close(door)
                servo.min()
                sleep(1)
                print("Door closed. Latch commanded closed (1500 us).")

    except (KeyboardInterrupt, EOFError):
        print("Test stopped. No automatic closing movement commanded.")
    finally:
        factory.close()


if __name__ == "__main__":
    main()
