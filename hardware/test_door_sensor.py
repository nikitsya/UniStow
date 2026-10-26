from gpiozero import Button
from time import sleep


def main():
    # GPIO17 is physical pin 11; the other sensor wire connects to GND.
    sensor = Button(17, pull_up=True, bounce_time=0.1)

    try:
        previous = None
        while True:
            state = sensor.is_pressed
            if state != previous:
                print("Contact closed" if state else "Contact open", flush=True)
                previous = state
            sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
