from enum import StrEnum


class ExpenseType(StrEnum):
    FOOD_DELIVERY = "Общепит/Доставка"
    DEBTS = "Задолженности"
    CLOTHING = "Одежда"
    HEALTH = "Здоровье/Медицина/Уход"
    BASIC_GROCERIES = "Продукты базовые"
    TREATS = "Вкусности"
    OTHER = "Другое"
    LUNCH = "Обед"
    MANDATORY_PAYMENTS = "Обязательные платежи/Подписки"
    LEISURE = "Досуг"
    GIFTS = "Подарки"
    HOME = "Дом"
    TRANSPORT = "Транспорт"
    PETS = "Домашние животные"
    PERSONAL = "Личные расходы"
    INVESTMENTS = "Инвестиции"
    LOAN_GIVEN = "Дал в долг"
    TRAVEL = "Путешествия"
    WIFE_SALARY = "Зарплата жены"
    GADGETS = "Гаджеты"
