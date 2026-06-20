#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Audio Visualizer for Leobog Hi75c Pro (or any OpenRGB keyboard)
Saves your USB bus from flooding and makes your keyboard react to system loopback audio.
Featuring advanced audio pattern recognition (beat-triggered wave reversal,
spectral flux tracking for speed/saturation, and dynamic genre-matching palette morphing).
"""

import sys
import time
import socket
import collections
import numpy as np
import soundcard as sc
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor, DeviceType

# ==============================================================================
# КОНСТАНТЫ НАСТРОЙКИ (КАЛИБРОВКА)
# ==============================================================================
FPS = 60                # Частота обновления клавиатуры (кадров в секунду)
SMOOTHING_BASS = 0.35   # Скорость спада баса (чем выше, тем быстрее диоды тухнут на спадах звука)
SMOOTHING_TREBLE = 0.19 # Скорость спада средних и высоких частот (для плавности картинки)
SENSITIVITY = 1.0       # Общий множитель чувствительности к звуку
LIGHT_DIFFUSION = 0.22  # Перетекание света между клавишами (0.0 - выкл, 0.22 - средний красивый размыв, 0.4 - размытие в кашу)

# Режим работы визуализатора:
# - 'spectrum_linear': Частоты разложены по клавишам (волна градиента слева направо).
# - 'reactive_pulse': Вся клавиатура синхронно пульсирует и меняет цвет под музыку.
# - 'hybrid_equalizer': Поклавишный спектр на фоне динамической фоновой подсветки.
# - 'standalone_pattern': Работает ТОЛЬКО как генератор эффектов (без захвата звука).
MODE = 'spectrum_linear'

# Интеллектуальное управление цветом (Распознавание паттернов в звуке):
# - DYNAMIC_PALETTE = True: Цветовая гамма плавно подстраивается под жанр музыки.
#   Басы и биты смещают гамму к огненному 'sunset', а вокал/высокие частоты — к прохладному 'ocean'.
# - DYNAMIC_PALETTE = False: Используется фиксированная палитра из константы PALETTE.
DYNAMIC_PALETTE = False

# Премиальная фиксированная палитра (если DYNAMIC_PALETTE = False):
# - 'cyberpunk', 'sunset', 'ocean', 'matrix', 'rainbow'
PALETTE = 'rainbow'

# Эффект при тишине или в режиме 'standalone_pattern':
# - 'wave': Бегущий градиент палитры по клавиатуре.
# - 'breath': Плавное затухание и разгорание всей клавиатуры.
# - 'matrix': Цифровой дождь Матрицы (стекающие капли в цветах палитры).
# - 'fire': Симуляция живого пламени (пламя разгорается снизу вверх).
# - 'none': Эффекты отключены (остается статичный BG_COLOR или темнота).
PATTERN = 'none'

# Автопереключение на паттерны при отсутствии звука в плеере
AUTO_SWITCH_TO_PATTERN = True
SILENCE_TIMEOUT = 5.0     # Время тишины в секундах, после которого включится паттерн
SILENCE_THRESHOLD = 0.0015 # Порог тишины (чувствительность обнаружения звука)

# Фоновый цвет (R, G, B) в полной тишине (когда эффекты выключены)
BG_COLOR = (0, 0, 0)   # Фоновый цвет по умолчанию — чёрный (клавиатура полностью гаснет в тишине)

# Частотный диапазон для анализа
MIN_FREQ = 25           # Минимальная частота (басы)
MAX_FREQ = 20000        # Максимальная частота (высокие частоты)

# Параметры подключения к OpenRGB
OPENRGB_HOST = '127.0.0.1'
OPENRGB_PORT = 6789
# ==============================================================================

# ==============================================================================
# ЦВЕТОВЫЕ ПАЛИТРЫ (ГРАДИЕНТЫ)
# ==============================================================================
PALETTES = {
    'cyberpunk': [
        (255, 0, 128),   # Неоновый розовый
        (128, 0, 255),   # Неоновый фиолетовый
        (0, 255, 255)    # Яркий бирюзовый
    ],
    'sunset': [
        (255, 50, 0),    # Огненный оранжевый
        (255, 0, 128),   # Розовая фуксия
        (80, 0, 180)     # Глубокий фиолетовый
    ],
    'ocean': [
        (0, 20, 120),    # Глубокий синий
        (0, 160, 220),   # Бирюзовая волна
        (0, 255, 130)    # Светлый мятный
    ],
    'matrix': [
        (0, 40, 0),      # Темный лесной
        (0, 255, 70),    # Яркий лайм
        (180, 255, 200)  # Бело-салатовый
    ],
    'rainbow': None      # Классическая радуга (рассчитывается через HSV)
}

def interpolate_color(color_a, color_b, t):
    """
    Линейная интерполяция между двумя цветами RGB.
    """
    r = int(color_a[0] + (color_b[0] - color_a[0]) * t)
    g = int(color_a[1] + (color_b[1] - color_a[1]) * t)
    b = int(color_a[2] + (color_b[2] - color_a[2]) * t)
    return RGBColor(r, g, b)

def get_palette_color(palette_name, t):
    """
    Получение цвета из выбранной палитры на позиции t (от 0.0 до 1.0).
    """
    t = max(0.0, min(1.0, t))
    palette = PALETTES.get(palette_name, PALETTES['cyberpunk'])
    
    if palette is None:
        return RGBColor.fromHSV(int(t * 360), 100, 100)
        
    n_colors = len(palette)
    if n_colors == 1:
        return RGBColor(*palette[0])
        
    scaled_t = t * (n_colors - 1)
    idx = int(scaled_t)
    frac = scaled_t - idx
    
    if idx >= n_colors - 1:
        return RGBColor(*palette[-1])
        
    return interpolate_color(palette[idx], palette[idx+1], frac)
# ==============================================================================


def get_loopback_microphone():
    """
    Надежное получение loopback-устройства для захвата системного звука.
    """
    try:
        speaker = sc.default_speaker()
        if speaker:
            return sc.get_microphone(id=speaker.name, include_loopback=True)
    except Exception:
        pass
    
    try:
        mics = sc.all_microphones(include_loopback=True)
        loopback_mics = [m for m in mics if getattr(m, 'isloopback', False)]
        if loopback_mics:
            return loopback_mics[0]
    except Exception:
        pass
    
    return sc.default_microphone()


def generate_pattern_colors(pattern_type, num_leds, matrix_map, bg_color_obj, t_state, matrix_state):
    """
    Генерирует массив цветов на основе выбранного паттерна.
    Поддерживает 2D-матрицу (если доступна) и 1D-линейный fallback.
    """
    colors = []
    
    if pattern_type == 'wave':
        for i in range(num_leds):
            pos = ((i / num_leds) - (t_state * 0.5)) % 1.0
            colors.append(get_palette_color(PALETTE, pos))
            
    elif pattern_type == 'breath':
        brightness = 0.05 + 0.95 * (np.sin(t_state * 2.0) + 1.0) / 2.0
        color_pos = (t_state * 0.05) % 1.0
        base_color = get_palette_color(PALETTE, color_pos)
        
        pulse_color = RGBColor(
            int(base_color.red * brightness),
            int(base_color.green * brightness),
            int(base_color.blue * brightness)
        )
        colors = [pulse_color for _ in range(num_leds)]
        
    elif pattern_type == 'matrix':
        colors = [bg_color_obj for _ in range(num_leds)]
        
        if matrix_map:
            R = len(matrix_map)
            C = len(matrix_map[0])
            drop_rows = matrix_state['matrix_drops']
            drop_speeds = matrix_state['matrix_speeds']
            
            for c in range(C):
                drop_rows[c] += drop_speeds[c]
                if drop_rows[c] >= R:
                    drop_rows[c] = np.random.randint(-12, 0)
                    drop_speeds[c] = np.random.uniform(0.1, 0.3)
                
                head = int(drop_rows[c])
                for r in range(R):
                    led_idx = matrix_map[r][c]
                    if led_idx is None or led_idx < 0:
                        continue
                    
                    dist = r - head
                    if dist == 0:
                        colors[led_idx] = RGBColor(255, 255, 255)
                    elif dist < 0 and dist > -8:
                        brightness = (8 + dist) / 8.0
                        t_color = (c / C + t_state * 0.03) % 1.0
                        base_color = get_palette_color(PALETTE, t_color)
                        colors[led_idx] = RGBColor(
                            int(base_color.red * brightness),
                            int(base_color.green * brightness),
                            int(base_color.blue * brightness)
                        )
        else:
            for i in range(num_leds):
                val = np.sin(i * 0.25 - t_state * 3.5)
                val = max(0.0, val)
                if val > 0.85:
                    colors[i] = RGBColor(255, 255, 255)
                elif val > 0.05:
                    brightness = val
                    t_color = (i / num_leds + t_state * 0.05) % 1.0
                    base_color = get_palette_color(PALETTE, t_color)
                    colors[i] = RGBColor(
                        int(base_color.red * brightness),
                        int(base_color.green * brightness),
                        int(base_color.blue * brightness)
                    )
                    
    elif pattern_type == 'fire':
        colors = [bg_color_obj for _ in range(num_leds)]
        heat = matrix_state['fire_heat']
        R = heat.shape[0] - 1
        C = heat.shape[1]
        
        heat[R] = np.random.randint(160, 256, size=C)
        
        for r in range(R):
            for c in range(C):
                c_left = max(0, c - 1)
                c_right = min(C - 1, c + 1)
                h = (heat[r+1][c] + heat[r+1][c_left] + heat[r+1][c_right]) / 3.0
                cooling = np.random.uniform(1.8, 5.2)
                heat[r][c] = max(0.0, h - cooling)
                
        if matrix_map:
            for r in range(R):
                for c in range(C):
                    led_idx = matrix_map[r][c]
                    if led_idx is None or led_idx < 0:
                        continue
                    h = heat[r][c]
                    if h > 190:
                        colors[led_idx] = RGBColor(255, 255, 255)
                    elif h > 15:
                        t_val = (h - 15) / 175.0
                        base_color = get_palette_color(PALETTE, t_val)
                        colors[led_idx] = RGBColor(
                            int(base_color.red * (0.15 + 0.85 * t_val)),
                            int(base_color.green * (0.15 + 0.85 * t_val)),
                            int(base_color.blue * (0.15 + 0.85 * t_val))
                        )
        else:
            if np.random.random() < 0.25:
                idx = np.random.randint(0, num_leds)
                heat[0][idx] = 255.0
                
            new_heat = np.copy(heat[0])
            for i in range(num_leds):
                i_left = max(0, i - 1)
                i_right = min(num_leds - 1, i + 1)
                h = (heat[0][i] + heat[0][i_left] + heat[0][i_right]) / 3.0
                new_heat[i] = max(0.0, h - 8.0)
            heat[0] = new_heat
            
            for i in range(num_leds):
                h = heat[0][i]
                if h > 200:
                    colors[i] = RGBColor(255, 255, 255)
                elif h > 15:
                    t_val = (h - 15) / 185.0
                    base_color = get_palette_color(PALETTE, t_val)
                    colors[i] = RGBColor(
                        int(base_color.red * t_val),
                        int(base_color.green * t_val),
                        int(base_color.blue * t_val)
                    )
                    
    elif pattern_type == 'none':
        # Отключаем эффекты, оставляем статичный фоновый цвет (или полную темноту, если BG_COLOR = (0,0,0))
        colors = [bg_color_obj for _ in range(num_leds)]
        
    return colors


def main():
    print("Инициализация аудио-визуализатора...")
    
    # 1. Подключение к OpenRGB SDK с резервным портом
    client = None
    connected_port = OPENRGB_PORT

    try:
        print(f"Попытка подключения к OpenRGB на 127.0.0.1:{OPENRGB_PORT}...")
        client = OpenRGBClient(address=OPENRGB_HOST, port=OPENRGB_PORT)
        if len(client.devices) == 0 and OPENRGB_PORT != 6742:
            print(f"Подключение к порту {OPENRGB_PORT} успешно, но устройств не обнаружено.")
            print("Пробуем подключиться к стандартному порту OpenRGB (6742)...")
            backup_client = OpenRGBClient(address=OPENRGB_HOST, port=6742)
            if len(backup_client.devices) > 0:
                client = backup_client
                connected_port = 6742
                print(f"Автоматическое переключение на порт 6742. Обнаружено устройств: {len(client.devices)}")
    except (ConnectionError, TimeoutError, socket.error) as e:
        if OPENRGB_PORT != 6742:
            try:
                print(f"Не удалось подключиться к порту {OPENRGB_PORT}. Пробуем стандартный порт 6742...")
                client = OpenRGBClient(address=OPENRGB_HOST, port=6742)
                connected_port = 6742
                print(f"Успешно подключено к порту 6742! Обнаружено устройств: {len(client.devices)}")
            except (ConnectionError, TimeoutError, socket.error):
                pass
        
        if not client:
            print(f"\n[Ошибка] Не удалось подключиться к OpenRGB серверу: {e}")
            print(f"Убедитесь, что приложение OpenRGB запущено и SDK сервер активен на порту {OPENRGB_PORT} или 6742.")
            print("Также проверьте настройки брандмауэра (файрвола).\n")
            sys.exit(1)

    print("Подключение к OpenRGB установлено.")

    # 2. Выбор устройства (клавиатуры)
    keyboard = None
    for dev in client.devices:
        if dev.type == DeviceType.KEYBOARD:
            if "leobog" in dev.name.lower() or "hi75" in dev.name.lower():
                keyboard = dev
                break

    if not keyboard:
        keyboards = client.get_devices_by_type(DeviceType.KEYBOARD)
        if keyboards:
            keyboard = keyboards[0]

    if not keyboard:
        if client.devices:
            keyboard = client.devices[0]

    if not keyboard:
        print("[Ошибка] На сервере OpenRGB не найдено ни одного устройства!")
        sys.exit(1)

    if not keyboard.zones:
        print(f"[Ошибка] У устройства '{keyboard.name}' отсутствуют зоны подсветки!")
        sys.exit(1)
        
    zone = max(keyboard.zones, key=lambda z: len(z.leds))
    num_leds = len(zone.leds)
    print(f"Выбрано устройство: {keyboard.name}")
    print(f"Выбрана зона: {zone.name} ({num_leds} светодиодов)")

    try:
        keyboard.set_mode('direct')
        print("Устройство переведено в режим прямого управления (Direct).")
    except Exception:
        print("[Предупреждение] Не удалось принудительно установить режим Direct. "
              "Убедитесь, что в OpenRGB выбран режим Direct/Прямой.")

    matrix_map = None
    if hasattr(zone, 'matrix_map') and zone.matrix_map:
        matrix_map = zone.matrix_map
        print(f"Обнаружена матричная сетка устройства: {len(matrix_map)} рядов, {len(matrix_map[0])} колонок.")
    else:
        print("Матричная сетка недоступна. Будет использоваться 1D-линейное распределение.")

    # Инициализация стейтов эффектов
    # Размер drops/speeds берём из реального количества столбцов матрицы (или num_leds для 1D)
    _n_cols = len(matrix_map[0]) if matrix_map else num_leds
    _n_rows = len(matrix_map)    if matrix_map else 1
    pattern_state = {
        'matrix_drops': np.random.randint(-15, 0, size=_n_cols, dtype=int).astype(float),
        'matrix_speeds': np.random.uniform(0.12, 0.35, size=_n_cols),
        # +2 строки запаса: одна для «дна» источника огня, одна — чтобы heat[r+1] не выходил за границу
        'fire_heat': np.zeros((_n_rows + 2, _n_cols))
    }

    bg_color_obj = RGBColor(*BG_COLOR)
    
    # 3. Настройка захвата звука
    SAMPLE_RATE = 44100
    mic = None
    CHUNK_SIZE = 1024
    
    if MODE != 'standalone_pattern':
        try:
            speaker = sc.default_speaker()
            SAMPLE_RATE = int(speaker.default_samplerate)
        except Exception:
            pass

        mic = get_loopback_microphone()
        print(f"Захват звука с устройства: {mic.name} (Частота: {SAMPLE_RATE} Гц)")
        CHUNK_SIZE = int(SAMPLE_RATE / FPS)
        
        # Предварительный расчет частотных корзин (Bins)
        freq_boundaries = np.logspace(np.log10(MIN_FREQ), np.log10(MAX_FREQ), num=num_leds + 1)
        bin_ranges = []
        for i in range(num_leds):
            f_start = freq_boundaries[i]
            f_end = freq_boundaries[i+1]
            bin_start = max(1, int(f_start * CHUNK_SIZE / SAMPLE_RATE))
            bin_end = max(bin_start + 1, int(f_end * CHUNK_SIZE / SAMPLE_RATE))
            bin_ranges.append((bin_start, bin_end))

        bass_bins = (max(1, int(20 * CHUNK_SIZE / SAMPLE_RATE)), max(2, int(250 * CHUNK_SIZE / SAMPLE_RATE)))
        mid_bins = (max(2, int(250 * CHUNK_SIZE / SAMPLE_RATE)), max(3, int(4000 * CHUNK_SIZE / SAMPLE_RATE)))
        high_bins = (max(3, int(4000 * CHUNK_SIZE / SAMPLE_RATE)), max(4, int(16000 * CHUNK_SIZE / SAMPLE_RATE)))

    # Переменные для сглаживания
    smoothed_amps = np.zeros(num_leds)
    smoothed_pulse = np.zeros(3)
    ref_volume = 0.1
    
    # Состояние анимации
    hue_offset = 0.0
    smooth_x = 0.0
    smooth_y = 0.0
    
    # Стейт для распознавания музыкальных паттернов
    prev_fft_amps = None
    smooth_flux = 0.0
    flux_max = 0.1
    smooth_balance = 0.5
    
    # Обнаружение бита (ударных) для разворота волны
    bass_history = collections.deque(maxlen=30)  # O(1) pop слева вместо O(n)
    beat_cooldown = 0
    wave_direction = 1.0  # 1.0 (вправо) или -1.0 (влево)
    beat_color_shift = 0.0  # Всплеск сдвига палитры при ударе баса
    
    # Для создания волн при вспышках частот
    wave_pulses = []
    high_history = collections.deque(maxlen=30)
    high_cooldown = 0
    mid_history = collections.deque(maxlen=30)
    mid_cooldown = 0
    
    # Переменные для автоопределения тишины
    in_silence = (MODE == 'standalone_pattern')
    silence_time = 0.0
    
    start_time = time.time()

    print(f"\nЗапуск визуализатора в режиме: {MODE}")
    if DYNAMIC_PALETTE and MODE != 'standalone_pattern':
        print("Включено автоопределение жанров (динамическое смешивание Sunset и Ocean)!")
    else:
        print(f"Используется фиксированная палитра: {PALETTE}")
    print("Нажмите Ctrl+C для выхода.")

    # 4. Основной рабочий цикл
    try:
        if MODE == 'standalone_pattern':
            while True:
                t_state = time.time() - start_time
                colors = generate_pattern_colors(PATTERN, num_leds, matrix_map, bg_color_obj, t_state, pattern_state)
                zone.set_colors(colors, fast=True)
                time.sleep(1.0 / FPS)
        else:
            with mic.recorder(samplerate=SAMPLE_RATE) as recorder:
                while True:
                    data = recorder.record(numframes=CHUNK_SIZE)
                    if len(data) == 0:
                        continue

                    # Сведение стерео в моно
                    if data.ndim > 1 and data.shape[1] > 1:
                        mono_data = np.mean(data, axis=1)
                    else:
                        mono_data = data.flatten()

                    n_samples = len(mono_data)
                    if n_samples == 0:
                        continue

                    # Проверка тишины (Silence Detection)
                    max_amp = np.max(np.abs(mono_data))
                    if max_amp < SILENCE_THRESHOLD:
                        silence_time += CHUNK_SIZE / SAMPLE_RATE
                        if silence_time >= SILENCE_TIMEOUT and AUTO_SWITCH_TO_PATTERN:
                            in_silence = True
                    else:
                        silence_time = 0.0
                        in_silence = False

                    if in_silence:
                        t_state = time.time() - start_time
                        colors = generate_pattern_colors(PATTERN, num_leds, matrix_map, bg_color_obj, t_state, pattern_state)
                    else:
                        # Обработка сигнала (FFT)
                        window = np.hanning(n_samples)
                        windowed_data = mono_data * window

                        fft_vals = np.fft.rfft(windowed_data)
                        fft_amps = np.abs(fft_vals)
                        
                        if len(fft_amps) > 1:
                            fft_amps = fft_amps[1:]
                        else:
                            continue

                        # ======================================================================
                        # DSP: РАСПОЗНАВАНИЕ МУЗЫКАЛЬНЫХ ПАТТЕРНОВ
                        # ======================================================================
                        
                        # 1. Расчет спектрального потока (Spectral Flux) - резкие переходы/удары
                        if prev_fft_amps is not None and len(prev_fft_amps) == len(fft_amps):
                            flux = np.sum(np.abs(fft_amps - prev_fft_amps))
                        else:
                            flux = 0.0
                        prev_fft_amps = np.copy(fft_amps)

                        # Сглаживание и нормализация потока
                        smooth_flux = 0.9 * smooth_flux + 0.1 * flux
                        flux_max = 0.99 * flux_max + 0.01 * max(smooth_flux, 1e-3)
                        norm_flux = min(1.0, smooth_flux / flux_max) if flux_max > 0 else 0.0

                        # Выделяем три широкие полосы
                        raw_bass = np.max(fft_amps[bass_bins[0]:bass_bins[1]]) if bass_bins[0] < len(fft_amps) else 0.0
                        raw_mid = np.max(fft_amps[mid_bins[0]:mid_bins[1]]) if mid_bins[0] < len(fft_amps) else 0.0
                        raw_high = np.max(fft_amps[high_bins[0]:high_bins[1]]) if high_bins[0] < len(fft_amps) else 0.0

                        raw_pulse = np.array([raw_bass, raw_mid, raw_high]) * SENSITIVITY

                        # Сглаживание по трем полосам с разделением
                        for i in range(3):
                            val = raw_pulse[i]
                            k_smooth = SMOOTHING_BASS if i == 0 else SMOOTHING_TREBLE
                            if val > smoothed_pulse[i]:
                                smoothed_pulse[i] = 0.85 * val + 0.15 * smoothed_pulse[i]
                            else:
                                smoothed_pulse[i] = k_smooth * val + (1.0 - k_smooth) * smoothed_pulse[i]

                        # Автоусиление (AGC)
                        pulse_max = np.max(smoothed_pulse)
                        ref_volume = 0.98 * ref_volume + 0.02 * max(pulse_max, 1e-4)
                        ref_volume = max(ref_volume, 0.01)

                        # ДБ-масштабирование трех полос
                        pulse_ratios = np.clip(smoothed_pulse / ref_volume, 1e-5, 1.0)
                        pulse_dbs = 20 * np.log10(pulse_ratios)
                        norm_pulse = np.clip((pulse_dbs + 30) / 30, 0.0, 1.0)
                        norm_bass, norm_mid, norm_high = norm_pulse[0], norm_pulse[1], norm_pulse[2]

                        # Затухание всплеска цвета от бита
                        beat_color_shift *= 0.88

                        # 2. Обнаружение бита (транзиента баса) для инверсии направления волны
                        if beat_cooldown > 0:
                            beat_cooldown -= 1
                        if high_cooldown > 0:
                            high_cooldown -= 1
                        if mid_cooldown > 0:
                            mid_cooldown -= 1

                        bass_history.append(norm_bass)
                        bass_avg = np.mean(bass_history) if len(bass_history) > 0 else 0.15

                        high_history.append(norm_high)
                        high_avg = np.mean(high_history) if len(high_history) > 0 else 0.15

                        mid_history.append(norm_mid)
                        mid_avg = np.mean(mid_history) if len(mid_history) > 0 else 0.15

                        # Если произошел резкий всплеск баса выше среднего уровня:
                        if norm_bass > bass_avg * 1.35 and norm_bass > 0.20 and beat_cooldown == 0:
                            wave_direction *= -1.0  # Разворачиваем движение градиента!
                            beat_cooldown = 8       # Задержка триггера (~260мс)
                            beat_color_shift = 65.0  # Внезапный сдвиг оттенка на 65 градусов при ударе баса!
                            # Запуск волны слева направо (амплитуда 0.5, скорость 1.8 светодиода/кадр)
                            wave_pulses.append({'pos': 0.0, 'dir': 1.0, 'amp': 0.5, 'speed': 1.8})

                        # Если произошел резкий всплеск средних частот выше среднего уровня:
                        if norm_mid > mid_avg * 1.40 and norm_mid > 0.20 and mid_cooldown == 0:
                            mid_cooldown = 8
                            # Запуск двух волн из центра в разные стороны
                            center_pos = float(num_leds // 2)
                            wave_pulses.append({'pos': center_pos, 'dir': -1.0, 'amp': 0.45, 'speed': 1.8})
                            wave_pulses.append({'pos': center_pos, 'dir': 1.0, 'amp': 0.45, 'speed': 1.8})

                        # Если произошел резкий всплеск высоких частот выше среднего уровня:
                        # Если произошел резкий всплеск высоких частот выше среднего уровня:
                        if norm_high > high_avg * 1.60 and norm_high > 0.35 and high_cooldown == 0:
                            high_cooldown = 18
                            # Запуск волны справа налево (более быстрая и мягкая)
                            wave_pulses.append({'pos': float(num_leds - 1), 'dir': -1.0, 'amp': 0.35, 'speed': 2.2})

                        # 3. Распознавание музыкального жанра (соотношение НЧ к СЧ/ВЧ)
                        bass_energy = norm_bass
                        treble_energy = (norm_mid + norm_high) / 2.0
                        total_energy = bass_energy + treble_energy
                        
                        if total_energy > 0.01:
                            # 1.0 - чистый басовый трек, 0.0 - чистый вокал/высокие
                            genre_balance = bass_energy / total_energy
                        else:
                            genre_balance = 0.5
                            
                        # Сглаживаем переход жанра
                        smooth_balance = 0.96 * smooth_balance + 0.04 * genre_balance

                        # ======================================================================
                        
                        colors = []

                        if MODE in ('spectrum_linear', 'hybrid_equalizer'):
                            raw_amps = np.zeros(num_leds)
                            for i in range(num_leds):
                                start, end = bin_ranges[i]
                                if start < len(fft_amps):
                                    actual_end = min(end, len(fft_amps))
                                    if start < actual_end:
                                        raw_amps[i] = np.max(fft_amps[start:actual_end])
                                    else:
                                        raw_amps[i] = fft_amps[start]
                                else:
                                    raw_amps[i] = 0.0

                                # Частотная компенсация: бас = 1.0, средние = 0.8, высокие = 1.8
                                # Квадратичная кривая для идеального попадания в три точки
                                x = i / num_leds
                                gain = 2.4 * (x ** 2) - 1.6 * x + 1.0
                                raw_amps[i] *= gain

                            raw_amps *= SENSITIVITY

                            # Сглаживание EMA с разделением на басы (быстрый спад) и СЧ/ВЧ (плавный спад)
                            for i in range(num_leds):
                                val = raw_amps[i]
                                # Для басового регистра (первые 25% светодиодов) делаем быстрый спад (SMOOTHING_BASS),
                                # чтобы моментально видеть малейшие проседания басового ритма.
                                k_smooth = SMOOTHING_BASS if i < int(num_leds * 0.25) else SMOOTHING_TREBLE
                                
                                if val > smoothed_amps[i]:
                                    smoothed_amps[i] = 0.85 * val + 0.15 * smoothed_amps[i]
                                else:
                                    smoothed_amps[i] = k_smooth * val + (1.0 - k_smooth) * smoothed_amps[i]

                            # Пространственная диффузия (перетекание света между соседними кнопками)
                            # Свечение «растекается» по клавиатуре из активных участков, сглаживая переходы
                            if LIGHT_DIFFUSION > 0.0:
                                padded = np.pad(smoothed_amps, 1, mode='edge')
                                diffused = (
                                    smoothed_amps * (1.0 - 2.0 * LIGHT_DIFFUSION) +
                                    padded[:-2] * LIGHT_DIFFUSION +
                                    padded[2:] * LIGHT_DIFFUSION
                                )
                            else:
                                diffused = np.copy(smoothed_amps)

                            ratios = diffused / ref_volume
                            norm_amps = np.clip(ratios, 0.0, 1.0)
                            norm_amps = norm_amps ** 1.35  # контрастный буст яркости

                            # Эффект клиппинга (перегрузки) частот
                            overload_offsets = np.zeros(num_leds)
                            color_white_bleed = np.zeros(num_leds)
                            for i in range(num_leds):
                                if ratios[i] > 1.0:
                                    overload = ratios[i] - 1.0
                                    # Растекание в зависимости от силы на ВСЮ клавиатуру
                                    spread_width = 1.5 + overload * 35.0
                                    spread_amp = min(overload * 0.7, 1.0)
                                    
                                    for j in range(num_leds):
                                        dist = abs(i - j)
                                        contrib = spread_amp * np.exp(-((dist / spread_width) ** 2))
                                        overload_offsets[j] = max(overload_offsets[j], contrib)
                                        
                                        # Плавное побеление эпицентра перегрузки (только для НЧ и СЧ, чтобы ВЧ не стробили)
                                        if dist <= 2 and i < int(num_leds * 0.75):
                                            white_contrib = min(overload * 0.6, 0.95) * (1.0 - dist / 3.0)
                                            color_white_bleed[j] = max(color_white_bleed[j], white_contrib)

                            # Добавляем заливку перегрузки в яркость
                            norm_amps = np.clip(norm_amps + overload_offsets, 0.0, 1.0)

                            # Обновление и отрисовка активных волн
                            active_waves = []
                            wave_offsets = np.zeros(num_leds)
                            for wave in wave_pulses:
                                wave['pos'] += wave['dir'] * wave['speed']
                                wave['amp'] *= 0.90  # Затухание амплитуды волны
                                
                                # Распределяем волну по клавишам (форма купола с шириной в 3 клавиши)
                                for i in range(num_leds):
                                    dist = abs(i - wave['pos'])
                                    contrib = wave['amp'] * np.exp(-(dist / 3.0) ** 2)
                                    wave_offsets[i] = max(wave_offsets[i], contrib)
                                    
                                if wave['amp'] > 0.02 and 0 <= wave['pos'] < num_leds:
                                    active_waves.append(wave)
                            wave_pulses = active_waves

                            # Смешиваем волны со спектром эквалайзера
                            norm_amps = np.clip(norm_amps + wave_offsets, 0.0, 1.0)

                            # Реактивная скорость вращения градиента
                            # Если выбрана радуга (rainbow), фиксируем цвета за клавишами/герцами,
                            # но качаем её фазу туда-сюда (wobble) на ±35 градусов в такт баса
                            if PALETTE == 'rainbow' and not DYNAMIC_PALETTE:
                                # Медленная фоновая ротация (накапливаем смещение)
                                rotation_speed = 0.08 * wave_direction
                                hue_offset = (hue_offset + rotation_speed) % 360
                                # Дополнительное покачивание от текущего уровня баса
                                current_wobble = norm_bass * 15.0 * wave_direction
                            else:
                                # Скорость вращения для других режимов
                                rotation_speed = (0.15 + norm_bass * 1.5 + norm_flux * 1.0) * wave_direction
                                hue_offset = (hue_offset + rotation_speed) % 360
                                current_wobble = 0.0

                            # Рассчитываем оттенок для фона
                            x = norm_bass * 1.0 + norm_mid * -0.5 + norm_high * -0.5
                            y = norm_bass * 0.0 + norm_mid * 0.866 + norm_high * -0.866
                            smooth_x = 0.95 * smooth_x + 0.05 * x
                            smooth_y = 0.95 * smooth_y + 0.05 * y
                            
                            if abs(smooth_x) > 1e-3 or abs(smooth_y) > 1e-3:
                                bg_hue = int(np.degrees(np.arctan2(smooth_y, smooth_x)) % 360)
                            else:
                                bg_hue = 240

                            # Отрисовка
                            for i in range(num_leds):
                                norm_amp = norm_amps[i]
                                # Объединяем медленное вращение, покачивание от баса и резкий сдвиг от бита
                                t = ((i / num_leds) + ((hue_offset + current_wobble + beat_color_shift) / 360.0)) % 1.0
                                
                                # Определение цвета active_color
                                if DYNAMIC_PALETTE:
                                    # Морфинг палитры: плавно смешиваем 'sunset' (под басы) и 'ocean' (под верха)
                                    color_warm = get_palette_color('sunset', t)
                                    color_cool = get_palette_color('ocean', t)
                                    active_color = interpolate_color(
                                        (color_cool.red, color_cool.green, color_cool.blue),
                                        (color_warm.red, color_warm.green, color_warm.blue),
                                        smooth_balance
                                    )
                                else:
                                    active_color = get_palette_color(PALETTE, t)

                                # Для радуги (rainbow) делаем красивое «подмешивание» (bleed)
                                # доминантного оттенка спектра, чтобы менять оттенки закрепленных цветов
                                if PALETTE == 'rainbow' and not DYNAMIC_PALETTE:
                                    bg_t = (bg_hue / 360.0) % 1.0
                                    bg_color_base = get_palette_color('rainbow', bg_t)
                                    # Сила подмешивания оттенка зависит от громкости баса и перепадов (до 30%)
                                    bleed_factor = 0.30 * max(norm_bass, norm_mid, norm_high)
                                    
                                    active_color = interpolate_color(
                                        (active_color.red, active_color.green, active_color.blue),
                                        (bg_color_base.red, bg_color_base.green, bg_color_base.blue),
                                        bleed_factor
                                    )

                                # Подмешиваем белый цвет в эпицентре перегрузки (клиппинга)
                                if color_white_bleed[i] > 0.0:
                                    active_color = interpolate_color(
                                        (active_color.red, active_color.green, active_color.blue),
                                        (255, 255, 255),
                                        color_white_bleed[i]
                                    )

                                if MODE == 'spectrum_linear':
                                    r = int(BG_COLOR[0] + (active_color.red - BG_COLOR[0]) * norm_amp)
                                    g = int(BG_COLOR[1] + (active_color.green - BG_COLOR[1]) * norm_amp)
                                    b = int(BG_COLOR[2] + (active_color.blue - BG_COLOR[2]) * norm_amp)
                                else: # hybrid_equalizer
                                    bg_t = (bg_hue / 360.0) % 1.0
                                    
                                    # Подложка также подстраивается под жанр
                                    if DYNAMIC_PALETTE:
                                        bg_warm = get_palette_color('sunset', bg_t)
                                        bg_cool = get_palette_color('ocean', bg_t)
                                        bg_color_base = interpolate_color(
                                            (bg_cool.red, bg_cool.green, bg_cool.blue),
                                            (bg_warm.red, bg_warm.green, bg_warm.blue),
                                            smooth_balance
                                        )
                                    else:
                                        bg_color_base = get_palette_color(PALETTE, bg_t)
                                        
                                    bg_volume = max(norm_bass, norm_mid, norm_high)
                                    bg_scale = 0.05 + 0.20 * bg_volume
                                    
                                    bg_color_active = RGBColor(
                                        int(bg_color_base.red * bg_scale),
                                        int(bg_color_base.green * bg_scale),
                                        int(bg_color_base.blue * bg_scale)
                                    )
                                    
                                    r = int(bg_color_active.red + (active_color.red - bg_color_active.red) * norm_amp)
                                    g = int(bg_color_active.green + (active_color.green - bg_color_active.green) * norm_amp)
                                    b = int(bg_color_active.blue + (active_color.blue - bg_color_active.blue) * norm_amp)

                                colors.append(RGBColor(r, g, b))

                        elif MODE == 'reactive_pulse':
                            # Пульсация
                            x = norm_bass * 1.0 + norm_mid * -0.5 + norm_high * -0.5
                            y = norm_bass * 0.0 + norm_mid * 0.866 + norm_high * -0.866
                            smooth_x = 0.9 * smooth_x + 0.1 * x
                            smooth_y = 0.9 * smooth_y + 0.1 * y

                            if abs(smooth_x) > 1e-3 or abs(smooth_y) > 1e-3:
                                current_hue = int(np.degrees(np.arctan2(smooth_y, smooth_x)) % 360)
                            else:
                                current_hue = 240
                            
                            t = (current_hue / 360.0) % 1.0
                            
                            if DYNAMIC_PALETTE:
                                base_warm = get_palette_color('sunset', t)
                                base_cool = get_palette_color('ocean', t)
                                base_color = interpolate_color(
                                    (base_cool.red, base_cool.green, base_cool.blue),
                                    (base_warm.red, base_warm.green, base_warm.blue),
                                    smooth_balance
                                )
                            else:
                                base_color = get_palette_color(PALETTE, t)
                            
                            overall_volume = max(norm_bass, norm_mid, norm_high)
                            # Насыщенность и яркость реагируют на перепады (Spectral Flux)
                            val = 0.05 + 0.95 * overall_volume

                            # Обновляем ref_volume и в режиме reactive_pulse
                            pulse_max = max(norm_bass, norm_mid, norm_high) * ref_volume
                            ref_volume = 0.98 * ref_volume + 0.02 * max(pulse_max, 1e-4)
                            ref_volume = max(ref_volume, 0.01)

                            pulse_color = RGBColor(
                                int(base_color.red * val),
                                int(base_color.green * val),
                                int(base_color.blue * val)
                            )
                            colors = [pulse_color for _ in range(num_leds)]

                    # Отправка кадров (только если colors непустой)
                    if colors:
                        zone.set_colors(colors, fast=True)

    except KeyboardInterrupt:
        print("\nЗавершение работы визуализатора (Ctrl+C)...")
    except Exception as e:
        print(f"\n[Критическая ошибка] Неожиданное исключение: {e}")
    finally:
        # Гасим клавиатуру при любом выходе — штатном или аварийном
        try:
            black_colors = [RGBColor(0, 0, 0) for _ in range(num_leds)]
            zone.set_colors(black_colors, fast=True)
            print("Светодиоды клавиатуры сброшены.")
        except Exception:
            pass
        print("Выход.")


if __name__ == '__main__':
    main()
