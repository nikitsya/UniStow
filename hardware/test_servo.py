from gpiozero import AngularServo
from gpiozero.pins.pigpio import PiGPIOFactory
from time import sleep

def main():
    CLOSED_ANGLE = 0
    OPEN_ANGLE = 90

    factory = PiGPIOFactory(host="localhost")

    # GPIO18 is physical pin 12.
    servo = AngularServo(
        18,
        min_angle=-90,
        max_angle=90,
        initial_angle=None,
        pin_factory=factory,
    )

    try:
        servo.angle = CLOSED_ANGLE
        sleep(1)
        print("Commanded to 0 degrees.")

        while True:
            command = input("Enter open, close or exit: ").strip().lower()

            if command == "open":
                servo.angle = OPEN_ANGLE
                print(f"Commanded to {OPEN_ANGLE} degrees.")

            elif command == "close":
                servo.angle = CLOSED_ANGLE
                print(f"Commanded to {CLOSED_ANGLE} degrees.")

            elif command == "exit":
                break

            else:
                print("Use open, close or exit.")

    except (KeyboardInterrupt, EOFError):
        pass

    finally:
        # Stop the signal without moving the latch.
        servo.close()
        factory.close()


if __name__ == "__main__":
    main()
