#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║   ДИАГНОСТИЧЕСКИЙ ИНСТРУМЕНТ ДЛЯ АБС SORL (А65R32.3538020)          ║
║   Протокол: K-Line (ISO 14230 / KWP2000)                             ║
║                                                                      ║
║   Разработано:                                                       ║
║   - vdd-pro.ru (инициатор проекта, полевые тесты)                    ║
║   - Qwen AI (исследователь протокола, разработчик кода)              ║
║                                                                      ║
║   Назначение:                                                        ║
║   Диагностика блоков АБС SORL, устанавливаемых на ГАЗель NEXT        ║
║   и другие коммерческие автомобили группы ГАЗ.                       ║
║                                                                      ║
║   История создания:                                                  ║
║   Этот скрипт был создан в ходе исследования проприетарного          ║
║   протокола блока АБС SORL Type 80. Стандартные диагностические      ║
║   сервисы (0x13, 0x18, 0x19) не поддерживаются блоком.               ║
║   Методом обратного инжиниринга были обнаружены:                     ║
║   - Нестандартная инициализация (200 бод + байт 0xF0)                ║
║   - Адрес блока 0x28                                                 ║
║   - Чтение дампа памяти через сервис 0x21, параметр 0x01             ║
║   - Local ID для идентификационных данных (0x91-0x97, 0x9A и др.)    ║
║   - Стирание ошибок через UDS Service 0x31, RoutineID 0x0201         ║
║     (Erase Mirror Memory DTC по стандарту ISO 14229)                 ║
║                                                                      ║
║   Лицензия: MIT                                                      ║
║   Используйте на свой страх и риск. Авторы не несут                  ║
║   ответственности за любые последствия использования.                ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import serial
import time
import sys

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================
PORT = 'COM5'           # COM-порт K-Line адаптера
BAUDRATE = 10400        # Скорость обмена (стандарт SORL)
TARGET_ABS = 0x28       # Адрес блока АБС
SOURCE_TESTER = 0xF1    # Адрес тестера

# ============================================================================
# ТАБЛИЦА КОДОВ ОШИБОК SORL
# ============================================================================
DTC_TABLE = {
    0xC3F0: "Ошибка главной шины CAN",
    0xC330: "Напряжение ЭБУ слишком высокое/низкое",
    0xC350: "Нет подключения электромагнитного клапана ABS",
    0xC360: "Ошибка EEPROM",
}

# ============================================================================
# БАЗОВЫЕ ФУНКЦИИ РАБОТЫ С ПРОТОКОЛОМ
# ============================================================================

def calc_checksum(data):
    """Контрольная сумма KWP2000: сумма всех байт mod 256"""
    return sum(data) % 256


def build_command(header, target, source, service, data_bytes=None):
    """Сборка диагностической команды с контрольной суммой"""
    cmd = [header, target, source, service]
    if data_bytes:
        cmd.extend(data_bytes)
    checksum = calc_checksum(cmd)
    cmd.append(checksum)
    return cmd


def send_and_read(ser, tx_bytes, wait_time=0.5):
    """Отправка команды и чтение ответа с учётом эха"""
    ser.reset_input_buffer()
    time.sleep(0.05)
    for b in tx_bytes:
        ser.write(b.to_bytes(1, 'big'))
        time.sleep(0.005)
    
    time.sleep(wait_time)
    rx_data = ser.read(512)
    
    if rx_data and len(rx_data) > len(tx_bytes):
        return rx_data[len(tx_bytes):]
    return rx_data


def tester_present(ser):
    """Поддержание диагностической сессии (сервис 0x3E)"""
    cmd = build_command(0x81, TARGET_ABS, SOURCE_TESTER, 0x3E)
    send_and_read(ser, cmd, wait_time=0.1)


def init_session(ser):
    """
    Инициализация сессии SORL
    Особенность: 200 бод + байт 0xF0 (фирменная реализация)
    """
    ser.baudrate = 200
    time.sleep(0.1)
    ser.reset_input_buffer()
    ser.write(b'\xf0')
    time.sleep(0.5)
    ser.baudrate = BAUDRATE
    time.sleep(0.1)

    session_cmd = build_command(0x81, TARGET_ABS, SOURCE_TESTER, 0x81)
    resp = send_and_read(ser, session_cmd, wait_time=0.5)
    
    if resp and len(resp) >= 4 and resp[3] == 0xC1:
        return True
    return False


def open_extended_session(ser):
    """Переход в расширенный диагностический сеанс"""
    tester_present(ser)
    cmd = build_command(0x82, TARGET_ABS, SOURCE_TESTER, 0x10, [0x81])
    resp = send_and_read(ser, cmd, wait_time=0.5)
    return resp and len(resp) >= 4 and resp[3] == 0x50


def read_lid(ser, lid):
    """Чтение Local ID через сервис 0x1A"""
    tester_present(ser)
    cmd = build_command(0x82, TARGET_ABS, SOURCE_TESTER, 0x1A, [lid])
    resp = send_and_read(ser, cmd, wait_time=0.5)
    
    if resp and len(resp) > 5 and resp[3] == 0x5A:
        return resp[5:-1]
    return None


def read_dump(ser):
    """Чтение дампа памяти через сервис 0x21, параметр 0x01"""
    tester_present(ser)
    cmd = build_command(0x82, TARGET_ABS, SOURCE_TESTER, 0x21, [0x01])
    resp = send_and_read(ser, cmd, wait_time=1.0)
    
    if resp and len(resp) > 5 and resp[3] == 0x61:
        return resp[5:-1]
    return None


# ============================================================================
# ФУНКЦИИ МЕНЮ
# ============================================================================

def print_header(title):
    """Красивый заголовок для разделов меню"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)


def decode_ascii(data):
    """Попытка декодировать байты как ASCII-строку"""
    try:
        s = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
        return s.strip()
    except:
        return None


def menu_read_identifiers(ser):
    """Чтение и отображение идентификационных данных блока"""
    print_header("📋 ИДЕНТИФИКАЦИОННЫЕ ДАННЫЕ БЛОКА АБС")
    
    lids = {
        0x91: "Артикул блока",
        0x92: "Версия ПО / модель авто",
        0x94: "Модель автомобиля",
        0x95: "Код завода / спецификация",
        0x97: "Тип блока АБС",
        0x9A: "Счётчик ошибок",
        0x9D: "Статус системы",
        0xE2: "Дополнительный статус",
        0xED: "Буфер данных (6 байт)",
    }
    
    for lid, description in lids.items():
        data = read_lid(ser, lid)
        if data:
            hex_str = ' '.join(f'{b:02X}' for b in data)
            ascii_str = decode_ascii(data)
            
            print(f"\n  [{lid:02X}] {description}:")
            print(f"      HEX:   {hex_str}")
            if ascii_str and any(c.isalpha() for c in ascii_str):
                print(f"      ASCII: {ascii_str}")
        else:
            print(f"\n  [{lid:02X}] {description}: ⚠️ Нет ответа")
    
    print()


def menu_read_dump(ser):
    """Чтение и отображение дампа памяти блока"""
    print_header("💾 ДАМП ПАМЯТИ БЛОКА (Service 0x21, param 0x01)")
    
    dump = read_dump(ser)
    if not dump:
        print("  ❌ Не удалось прочитать дамп!")
        return
    
    print(f"\n  Размер: {len(dump)} байт\n")
    print("  Адрес  HEX                                              ASCII")
    print("  " + "-"*66)
    
    for i in range(0, len(dump), 16):
        chunk = dump[i:i+16]
        hex_str = ' '.join(f'{b:02X}' for b in chunk)
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f"  {i:04X}   {hex_str:<48}  {ascii_str}")
    
    # Анализ ключевых байтов
    print("\n  📊 Анализ ключевых байтов:")
    print(f"     Байт [02] = 0x{dump[2]:02X} (возможно, режим)")
    print(f"     Байт [05] = 0x{dump[5]:02X} (индекс/указатель ошибки)")
    print(f"     Байты [0E-10] = {' '.join(f'{b:02X}' for b in dump[0x0E:0x11])} (возможный код ошибки)")
    
    # Попытка интерпретации кода ошибки
    error_code = (dump[0x0E] << 8) | dump[0x0F]
    if error_code in DTC_TABLE:
        print(f"\n  🔍 Возможная ошибка: {DTC_TABLE[error_code]}")
    
    # Чтение счётчика ошибок
    counter = read_lid(ser, 0x9A)
    if counter and len(counter) >= 2:
        count = (counter[0] << 8) | counter[1]
        print(f"\n  📈 Счётчик ошибок (LID 0x9A): {count}")
    
    print()


def try_routine_control(ser):
    """
    Стирание ошибок через Routine Control (UDS Service 0x31)
    
    РАБОЧАЯ КОМАНДА: 0x31 0x02 0x01
    Это стандартный UDS-сервис с RoutineIdentifier 0x0201
    (Erase Mirror Memory DTC по ISO 14229).
    """
    print("\n  🧪 Стирание через Routine Control (Service 0x31)...")
    
    # Рабочая команда (проверена на практике!)
    primary_cmd = ([0x02, 0x01], "Erase Mirror Memory DTC (ISO 14229)")
    
    # Запасные варианты
    fallback_variants = [
        ([0x02, 0x02], "Erase Persistent DTC"),
        ([0x02, 0x03], "Erase DTC Info"),
        ([0xFF, 0x00], "Общий сброс 0xFF00"),
        ([0x02, 0x01, 0xFF], "Erase Mirror + option 0xFF"),
    ]
    
    # Пробуем основную команду
    params, desc = primary_cmd
    tester_present(ser)
    cmd = build_command(0x83, TARGET_ABS, SOURCE_TESTER, 0x31, params)
    resp = send_and_read(ser, cmd, wait_time=1.5)
    
    if resp and len(resp) >= 4 and resp[3] == 0x71:
        print(f"      ✅ {desc}: УСПЕХ!")
        print("      🔊 Если вы слышали гудение насоса ~3 сек — это нормально!")
        return True
    elif resp:
        # ВАЖНО: теперь печатаем ответ блока для диагностики!
        print(f"      ⚠️  Основная команда не сработала.")
        print(f"         Ответ блока: {' '.join(f'{b:02X}' for b in resp)}")
        if len(resp) >= 5 and resp[3] == 0x7F:
            error_code = resp[5]
            if error_code == 0x11:
                print(f"         Код 0x11: сервис не поддерживается")
            elif error_code == 0x12:
                print(f"         Код 0x12: подфункция не поддерживается")
            elif error_code == 0x22:
                print(f"         Код 0x22: условия не выполнены")
            elif error_code == 0x31:
                print(f"         Код 0x31: запрос вне диапазона")
            elif error_code == 0x72:
                print(f"         Код 0x72: общая ошибка выполнения")
            else:
                print(f"         Код ошибки: 0x{error_code:02X}")
    else:
        print(f"      ⏳ Основная команда: нет ответа от блока")
    
    print(f"      Пробуем альтернативы...")
    
    # Пробуем запасные варианты
    for params, desc in fallback_variants:
        tester_present(ser)
        if len(params) == 2:
            cmd = build_command(0x83, TARGET_ABS, SOURCE_TESTER, 0x31, params)
        else:
            cmd = build_command(0x84, TARGET_ABS, SOURCE_TESTER, 0x31, params)
        
        resp = send_and_read(ser, cmd, wait_time=1.5)
        
        if resp and len(resp) >= 4 and resp[3] == 0x71:
            print(f"      ✅ {desc}: УСПЕХ!")
            return True
        elif resp:
            print(f"      ⚠️  {desc}: ответ {' '.join(f'{b:02X}' for b in resp)}")
        else:
            print(f"      ⏳ {desc}: нет ответа")
    
    return False

def menu_clear_errors(ser):
    """Стирание ошибок с проверкой результата"""
    print_header("🧹 СТИРАНИЕ ОШИБОК")
    
    # Читаем счётчик ДО стирания
    print("\n  📊 Проверка текущего состояния...")
    counter_before = read_lid(ser, 0x9A)
    if counter_before and len(counter_before) >= 2:
        count_before = (counter_before[0] << 8) | counter_before[1]
        print(f"     Счётчик ошибок ДО: {count_before}")
    else:
        count_before = -1
        print("     Счётчик ошибок: не удалось прочитать")
    
    dump_before = read_dump(ser)
    if dump_before:
        print(f"     Байт [05] ДО: 0x{dump_before[5]:02X}")
    
    print("\n  ⚠️  ВНИМАНИЕ:")
    print("     При успешном стирании блок АБС включит насос на ~3 секунды.")
    print("     Это нормальное поведение — блок проводит самодиагностику.\n")
    
    input("  Нажмите ENTER для начала стирания...")
    
    # ВАЖНО: Открываем расширенный диагностический сеанс
    # Именно это было в рабочей версии!
    print("\n  🔓 Открытие расширенного диагностического сеанса...")
    if open_extended_session(ser):
        print("     ✅ Расширенный сеанс открыт")
    else:
        print("     ⚠️  Не удалось открыть расширенный сеанс, продолжаем...")
    
    # Попытка стирания через Routine Control
    success = try_routine_control(ser)
    
    if success:
        # Ждём завершения процедуры в блоке
        print("\n  ⏳ Ожидание завершения процедуры в блоке...")
        time.sleep(4)
        
        # Проверяем результат
        print("\n  📊 Проверка результата...")
        counter_after = read_lid(ser, 0x9A)
        if counter_after and len(counter_after) >= 2:
            count_after = (counter_after[0] << 8) | counter_after[1]
            print(f"     Счётчик ошибок ПОСЛЕ: {count_after}")
            
            if count_before >= 0:
                if count_after < count_before:
                    print(f"     ✅ Счётчик уменьшился! Ошибки стёрты.")
                elif count_after == 0:
                    print(f"     ✅ Все ошибки стёрты!")
                else:
                    print(f"     ⚠️  Счётчик не изменился.")
        else:
            print("     Не удалось прочитать счётчик после стирания")
        
        dump_after = read_dump(ser)
        if dump_after and dump_before:
            print(f"     Байт [05] ПОСЛЕ: 0x{dump_after[5]:02X}")
            if dump_before[5] != dump_after[5]:
                print(f"     ✅ Дамп изменился (0x{dump_before[5]:02X} → 0x{dump_after[5]:02X})")
        
        print("\n  💡 Рекомендации:")
        print("     - Выключите и включите зажигание")
        print("     - Проверьте, погасла ли лампочка АБС")
        print("     - Если лампочка горит — возможно, неисправность активна")
        print("       (например, неисправный датчик)")
    else:
        print("\n  ❌ Не удалось стереть ошибки.")
        print("     Возможные причины:")
        print("     - Активная неисправность (блок не даёт стереть)")
        print("     - Требуется фирменный тестер SORL")
        print("     - Блок в защищённом режиме")
    
    print()


def menu_test_sensor(ser):
    """Тест: сравнение дампов для определения датчиков"""
    print_header("🔧 ТЕСТ ДАТЧИКОВ (сравнение дампов)")
    
    print("\n  📋 Инструкция:")
    print("     1. Будет прочитан текущий дамп")
    print("     2. Отключите один датчик АБС")
    print("     3. Включите зажигание, подождите 10 сек")
    print("     4. Нажмите ENTER — будет прочитан новый дамп")
    print("     5. Сравнивая дампы, можно определить, какой байт")
    print("        отвечает за какой датчик\n")
    
    input("  Нажмите ENTER для чтения первого дампа...")
    
    dump1 = read_dump(ser)
    if not dump1:
        print("  ❌ Не удалось прочитать дамп!")
        return
    
    print("\n  ✅ Первый дамп сохранён")
    print(f"     Байт [05] = 0x{dump1[5]:02X}")
    
    input("\n  Теперь отключите датчик и нажмите ENTER...")
    
    time.sleep(1)
    dump2 = read_dump(ser)
    if not dump2:
        print("  ❌ Не удалось прочитать дамп!")
        return
    
    print("\n  ✅ Второй дамп получен")
    print(f"     Байт [05] = 0x{dump2[5]:02X}")
    
    print("\n  📊 Изменения:")
    changes_found = False
    for i in range(min(len(dump1), len(dump2))):
        if dump1[i] != dump2[i]:
            print(f"     Байт [{i:02X}]: 0x{dump1[i]:02X} → 0x{dump2[i]:02X}")
            changes_found = True
    
    if not changes_found:
        print("     ⚠️ Изменений не обнаружено")
    
    print()


def main_menu():
    """Главное меню программы"""
    print("\n" + "█"*70)
    print("█" + " "*68 + "█")
    print("█   ДИАГНОСТИЧЕСКИЙ ИНСТРУМЕНТ ДЛЯ АБС SORL (А65R32.3538020)   █")
    print("█                  ГАЗель NEXT / Школьный автобус              █")
    print("█                                                                █")
    print("█   Разработано: vdd-pro.ru + Qwen AI                           █")
    print("█" + " "*68 + "█")
    print("█"*70)
    
    while True:
        print("\n" + "─"*70)
        print("  ГЛАВНОЕ МЕНЮ:")
        print("─"*70)
        print("  [1] 📋 Прочитать идентификационные данные блока")
        print("  [2] 💾 Прочитать дамп памяти (32 байта)")
        print("  [3] 🧹 Стереть ошибки (Routine Control 0x31)")
        print("  [4] 🔧 Тест датчиков (сравнение дампов)")
        print("  [5] ℹ️  Информация о программе")
        print("  [0] 🚪 Выход")
        print("─"*70)
        
        choice = input("  Ваш выбор: ").strip()
        
        if choice == '0':
            print("\n  👋 До свидания! Удачи с ремонтом!\n")
            break
        
        # Подключение к блоку
        print(f"\n  🔌 Подключение к порту {PORT}...")
        try:
            ser = serial.Serial(PORT, BAUDRATE, timeout=0.1)
        except Exception as e:
            print(f"  ❌ Ошибка открытия порта: {e}")
            continue
        
        print("  🔧 Инициализация K-Line...")
        if not init_session(ser):
            print("  ❌ Не удалось установить связь с блоком!")
            print("     Проверьте:")
            print("     - Подключение K-Line адаптера")
            print("     - Включено ли зажигание")
            print("     - Правильность COM-порта")
            ser.close()
            continue
        
        print("  ✅ Связь установлена!\n")
        
        try:
            if choice == '1':
                menu_read_identifiers(ser)
            elif choice == '2':
                menu_read_dump(ser)
            elif choice == '3':
                menu_clear_errors(ser)
            elif choice == '4':
                menu_test_sensor(ser)
            elif choice == '5':
                print_header("ℹ️  О ПРОГРАММЕ")
                print("""
  Разработана для диагностики АБС SORL А65R32.3538020
  коммерческих автомобилей ГАЗель NEXT.
  
  Авторы:
    - vdd-pro.ru (инициатор проекта, полевые тесты)
    - Qwen AI (исследователь протокола, разработчик кода)
  
  Используемый протокол:
    - Физический уровень: K-Line (ISO 9141)
    - Скорость: 10400 бод
    - Протокол: KWP2000 (ISO 14230) с фирменными расширениями SORL
    - Адрес блока: 0x28
  
  Особенности реализации SORL:
    - Инициализация через 200 бод + байт 0xF0
    - Чтение дампа памяти через сервис 0x21, параметр 0x01
    - Стирание ошибок через UDS Service 0x31, RoutineID 0x0201
      (Erase Mirror Memory DTC по стандарту ISO 14229)
    - Ошибки хранятся в энергонезависимой памяти
  
  Известные Local ID:
    0x91 - Артикул блока
    0x92 - Версия ПО
    0x94 - Модель авто
    0x95 - Код спецификации
    0x97 - Тип блока
    0x9A - Счётчик ошибок
    0x9D - Статус системы
    0xE2 - Дополнительный статус
    0xED - Буфер данных
  
  Как помочь проекту:
    Если вы нашли рабочие команды для дополнительных функций
    или расшифровали новые Local ID — пожалуйста, поделитесь
    информацией через GitHub Issues!
    
  Лицензия: MIT
  Используйте на свой страх и риск.
""")
            else:
                print("\n  ⚠️ Неверный выбор. Попробуйте снова.")
        finally:
            ser.close()


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\n  ⚠️ Прервано пользователем. До свидания!\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ❌ Критическая ошибка: {e}")
        sys.exit(1)