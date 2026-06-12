from __future__ import annotations

import re
from difflib import get_close_matches
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExpenseType(StrEnum):
    """Canonical expense category identifiers used in business logic."""

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


class ExpenseCategoryMeta(BaseModel):
    """Validated metadata describing one expense category."""

    model_config = ConfigDict(extra="forbid")

    description: str
    aliases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    inclusion_rules: list[str] = Field(default_factory=list)
    exclusion_rules: list[str] = Field(default_factory=list)


class ExpenseCategoryForLLM(BaseModel):
    """Serializable category payload for LLM prompts and ranking tasks."""

    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    description: str
    aliases: list[str]
    examples: list[str]
    inclusion_rules: list[str]
    exclusion_rules: list[str]


EXPENSE_CATEGORIES: dict[ExpenseType, ExpenseCategoryMeta] = {
    ExpenseType.FOOD_DELIVERY: ExpenseCategoryMeta(
        description="Платная еда вне дома: кафе, рестораны, доставка и готовая еда на заказ.",
        aliases=[
            "общепит",
            "доставка",
            "доставка еды",
            "кафе",
            "ресторан",
            "рестораны",
            "кофейня",
            "кофе",
            "бургер",
            "пицца",
            "роллы",
            "takeaway",
            "еда вне дома",
        ],
        examples=["пицца 1200", "кофе в кофейне 290", "ужин в ресторане 2300"],
        inclusion_rules=[
            "Используй для заказов доставки, кафе, ресторанов и любой готовой еды вне дома.",
            "Сюда же относятся напитки и десерты, купленные в кафе или сервисе доставки.",
        ],
        exclusion_rules=[
            "Не используй для обычного рабочего обеда, если сообщение явно про обед как прием пищи днем.",
            "Не используй для покупок продуктов домой в магазине.",
        ],
    ),
    ExpenseType.DEBTS: ExpenseCategoryMeta(
        description="Платежи по долгам, кредитам, займам и закрытие задолженностей.",
        aliases=["долг", "задолженность", "кредит", "займ", "рассрочка", "ипотека"],
        examples=["платеж по кредиту 15000", "закрыл долг 3000"],
        inclusion_rules=["Используй, когда деньги идут на возврат ранее взятых обязательств."],
        exclusion_rules=["Не используй, если пользователь сам дал кому-то в долг."],
    ),
    ExpenseType.CLOTHING: ExpenseCategoryMeta(
        description="Одежда, обувь и аксессуары для ношения.",
        aliases=["одежда", "обувь", "вещи", "куртка", "футболка", "штаны", "кроссовки"],
        examples=["кроссовки 5600", "куртка 8900"],
        inclusion_rules=["Используй для одежды, обуви и носимых аксессуаров."],
        exclusion_rules=["Не используй для гаджетов и бытовых предметов."],
    ),
    ExpenseType.HEALTH: ExpenseCategoryMeta(
        description="Медицина, здоровье, уход за собой, лекарства и расходники медицинского назначения.",
        aliases=[
            "здоровье",
            "медицина",
            "уход",
            "аптека",
            "лекарства",
            "врач",
            "анализы",
            "линзы",
            "стоматолог",
            "психотерапия",
        ],
        examples=["линзы 523", "аптека 870", "прием врача 2500"],
        inclusion_rules=[
            "Используй для лекарств, врачей, клиник, аптек и товаров ухода.",
            "Сюда же относятся линзы, витамины и медицинские расходники.",
        ],
        exclusion_rules=["Не используй для косметики и личных покупок без явного медицинского или уходового смысла."],
    ),
    ExpenseType.BASIC_GROCERIES: ExpenseCategoryMeta(
        description="Базовые продукты домой: повседневная еда из супермаркета и магазина.",
        aliases=[
            "продукты",
            "продукты базовые",
            "супермаркет",
            "магазин",
            "гипермаркет",
            "пятерочка",
            "перекресток",
            "лента",
            "магнит",
            "еда домой",
        ],
        examples=["продукты 2150", "супермаркет 3400", "купил молоко и хлеб 430"],
        inclusion_rules=[
            "Используй для основных продуктовых покупок домой: хлеб, крупы, мясо, овощи, молоко и похожие товары.",
            "Если пользователь пишет просто 'продукты', по умолчанию это эта категория.",
        ],
        exclusion_rules=[
            "Не используй для сладостей, снеков и покупок ради удовольствия, если акцент на вкусностях.",
            "Не используй для кафе, ресторанов и доставки еды.",
        ],
    ),
    ExpenseType.TREATS: ExpenseCategoryMeta(
        description="Небазовые вкусные покупки: сладости, снеки, десерты и импульсивные лакомства.",
        aliases=["вкусности", "сладкое", "снеки", "десерт", "шоколад", "мороженое", "чипсы"],
        examples=["мороженое 180", "сладкое 450", "чипсы и кола 320"],
        inclusion_rules=[
            "Используй, когда покупка не про базовое питание, а про удовольствие, перекус или десерт.",
            "Подходит для сладкого, снеков и отдельных вкусняшек из магазина.",
        ],
        exclusion_rules=[
            "Не используй для основной продуктовой корзины домой.",
            "Не используй для кафе и ресторанов, если покупка была как общепит.",
        ],
    ),
    ExpenseType.OTHER: ExpenseCategoryMeta(
        description="Запасная категория для трат, которые не удается уверенно отнести ни к одной другой категории.",
        aliases=["другое", "прочее", "разное"],
        examples=["прочие расходы 700", "что-то по мелочи 250"],
        inclusion_rules=[
            "Используй только когда ни одна специализированная категория не подходит уверенно.",
            "Это последняя резервная категория.",
        ],
        exclusion_rules=[
            "Не используй, если расход можно отнести к личным расходам, дому, гаджетам или другой конкретной категории.",
            "Не используй вместо Personal для обычных персональных мелочей.",
        ],
    ),
    ExpenseType.LUNCH: ExpenseCategoryMeta(
        description="Рабочий или повседневный обед как отдельный прием пищи, обычно дневной.",
        aliases=["обед", "ланч", "бизнес-ланч", "бизнес ланч"],
        examples=["обед 650", "бизнес-ланч 780"],
        inclusion_rules=[
            "Используй, когда пользователь явно пишет 'обед' или 'ланч'.",
            "Категория нужна для регулярного учета дневных приемов пищи отдельно от остального общепита.",
        ],
        exclusion_rules=[
            "Не используй для кафе, ресторанов и доставки без явного указания на обед.",
            "Не используй для продуктовых покупок домой.",
        ],
    ),
    ExpenseType.MANDATORY_PAYMENTS: ExpenseCategoryMeta(
        description="Регулярные обязательные платежи и подписки: сервисы, коммуналка, аренда и похожие списания.",
        aliases=[
            "обязательные платежи",
            "подписки",
            "подписка",
            "коммуналка",
            "аренда",
            "интернет",
            "связь",
            "spotify",
            "youtube premium",
        ],
        examples=["подписка 299", "коммуналка 5400", "интернет 890", "мтс 350", "vpn 500"],
        inclusion_rules=["Используй для повторяющихся и обязательных списаний."],
        exclusion_rules=["Не используй для разовых развлечений и спонтанных покупок."],
    ),
    ExpenseType.LEISURE: ExpenseCategoryMeta(
        description="Развлечения, досуг и приятное времяпрепровождение.",
        aliases=["досуг", "развлечения", "кино", "театр", "игры", "концерт", "музей"],
        examples=["кино 900", "игра в steam 1600"],
        inclusion_rules=["Используй для отдыха, развлечений и культурных активностей."],
        exclusion_rules=["Не используй для обязательных подписок, если это регулярный сервисный платеж."],
    ),
    ExpenseType.GIFTS: ExpenseCategoryMeta(
        description="Подарки другим людям и расходы на их подготовку.",
        aliases=["подарки", "подарок", "букет", "сувенир"],
        examples=["подарок маме 3000", "букет 2200"],
        inclusion_rules=["Используй, когда покупка делается как подарок или знак внимания."],
        exclusion_rules=["Не используй для личных покупок пользователя."],
    ),
    ExpenseType.HOME: ExpenseCategoryMeta(
        description="Товары и расходы для дома, быта, ремонта и обустройства пространства.",
        aliases=[
            "дом",
            "для дома",
            "быт",
            "ремонт",
            "мебель",
            "посуда",
            "лампочка",
            "хозяйственное",
            "уборка",
        ],
        examples=["швабра 1200", "посуда 2400", "полка домой 3900"],
        inclusion_rules=[
            "Используй для бытовых предметов, мебели, ремонта и вещей для квартиры или дома.",
            "Сюда же относятся хозяйственные товары и предметы обустройства.",
        ],
        exclusion_rules=[
            "Не используй для электроники, компьютеров, телефонов и аксессуаров к ним: это Гаджеты.",
            "Не используй для личных вещей, если покупка не для дома как пространства.",
        ],
    ),
    ExpenseType.TRANSPORT: ExpenseCategoryMeta(
        description="Передвижение и содержание транспорта: такси, метро, автобусы, самокаты, каршеринг, а также обслуживание машины, ремонт, бензин и запчасти.",
        aliases=["транспорт", "такси", "метро", "автобус", "самокат", "каршеринг", "электричка", "бензин", "обслуживание машины", "ремонт машины", "запчасти"],
        examples=["такси 480", "самокат 420", "метро 62"],
        inclusion_rules=["Используй для любых расходов на перемещение из точки А в точку Б.", "Сюда же относятся бензин, обслуживание машины, ремонт автомобиля и покупка запчастей."],
        exclusion_rules=["Не используй для путешествий на отпуск или поездку с проживанием: это Путешествия."],
    ),
    ExpenseType.PETS: ExpenseCategoryMeta(
        description="Расходы на домашних животных: корм, ветеринар, аксессуары и уход.",
        aliases=["животные", "домашние животные", "кот", "кошка", "собака", "корм", "ветеринар", "наполнитель"],
        examples=["корм коту 1400", "ветеринар 3500"],
        inclusion_rules=["Используй для любых расходов, напрямую связанных с питомцами."],
        exclusion_rules=["Не используй для бытовых расходов дома, если трата не про животных."],
    ),
    ExpenseType.PERSONAL: ExpenseCategoryMeta(
        description="Личные расходы пользователя, которые относятся к личному комфорту и не подходят под более точечные категории.",
        aliases=["личные расходы", "личное", "косметика", "гигиена", "личный уход", "мелочи для себя"],
        examples=["шампунь 420", "косметика 1800", "мелочи для себя 950"],
        inclusion_rules=[
            "Используй для персональных покупок для себя: косметика, гигиена, небольшие личные вещи.",
            "Подходит, когда расход личный, но не медицинский, не одежда, не гаджет и не дом.",
        ],
        exclusion_rules=[
            "Не используй как запасную категорию для неизвестных трат: для этого есть Другое.",
            "Не используй для медицины и товаров ухода с явным лечебным смыслом: это Здоровье/Медицина/Уход.",
        ],
    ),
    ExpenseType.INVESTMENTS: ExpenseCategoryMeta(
        description="Покупка инвестиционных активов и пополнение брокерских счетов.",
        aliases=["инвестиции", "инвестирование", "акции", "облигации", "etf", "брокер", "иис"],
        examples=["купил акции 10000", "пополнил брокерский счет 5000"],
        inclusion_rules=["Используй для вложений в финансовые инструменты."],
        exclusion_rules=["Не используй для обычных накоплений без инвестирования, если это не следует из текста."],
    ),
    ExpenseType.LOAN_GIVEN: ExpenseCategoryMeta(
        description="Деньги, которые пользователь дал в долг другому человеку.",
        aliases=["дал в долг", "одолжил", "одолжил другу", "дал деньги"],
        examples=["дал в долг 5000", "одолжил брату 2000"],
        inclusion_rules=["Используй, когда пользователь передает деньги другому человеку как заем."],
        exclusion_rules=["Не используй для погашения собственных кредитов и задолженностей."],
    ),
    ExpenseType.TRAVEL: ExpenseCategoryMeta(
        description="Путешествия, поездки, отпускные расходы, билеты и проживание.",
        aliases=["путешествия", "поездка", "отель", "билеты", "авиабилеты", "отпуск", "бронь"],
        examples=["отель 12000", "авиабилеты 18500"],
        inclusion_rules=["Используй для расходов, связанных с поездками, отпуском и путешествием как событием."],
        exclusion_rules=["Не используй для обычного городского транспорта и такси в повседневной жизни."],
    ),
    ExpenseType.WIFE_SALARY: ExpenseCategoryMeta(
        description="Отдельная категория для учета зарплаты жены как особого денежного потока.",
        aliases=["зарплата жены", "зп жены", "доход жены"],
        examples=["зарплата жены 120000"],
        inclusion_rules=["Используй только при явном упоминании зарплаты жены."],
        exclusion_rules=["Не используй для обычных трат и любых других доходов."],
    ),
    ExpenseType.GADGETS: ExpenseCategoryMeta(
        description="Электроника, техника, гаджеты и аксессуары к ним.",
        aliases=[
            "гаджеты",
            "техника",
            "электроника",
            "ноутбук",
            "телефон",
            "смартфон",
            "пк",
            "монитор",
            "клавиатура",
            "мышка",
            "зарядка",
        ],
        examples=["мышка 3500", "монитор 22000", "зарядка 1900"],
        inclusion_rules=[
            "Используй для электроники, цифровой техники и аксессуаров к устройствам.",
            "Сюда же относятся комплектующие ПК и периферия.",
        ],
        exclusion_rules=[
            "Не используй для мебели, посуды, ремонта и обычных бытовых товаров: это Дом.",
            "Не используй для хозяйственных покупок, даже если они сделаны для квартиры.",
        ],
    ),
}


EXPENSE_TYPE_VALUES = [expense_type.value for expense_type in ExpenseType]


def _validate_category_mapping() -> None:
    missing = [expense_type for expense_type in ExpenseType if expense_type not in EXPENSE_CATEGORIES]
    if missing:
        missing_codes = ", ".join(expense_type.name for expense_type in missing)
        raise RuntimeError(f"Metadata is missing for categories: {missing_codes}")


_validate_category_mapping()


def normalize_text(value: str) -> str:
    """Normalize a category-like text for lookup and fuzzy matching."""

    normalized = value.strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", normalized)


def build_expense_type_lookup() -> dict[str, ExpenseType]:
    """Build a normalized lookup from canonical names and aliases to ExpenseType."""

    lookup: dict[str, ExpenseType] = {}
    for expense_type in ExpenseType:
        meta = EXPENSE_CATEGORIES[expense_type]
        lookup[normalize_text(expense_type.value)] = expense_type
        for alias in meta.aliases:
            lookup[normalize_text(alias)] = expense_type
    return lookup


EXPENSE_TYPE_LOOKUP = build_expense_type_lookup()


def resolve_expense_type(raw_expense_type: str) -> ExpenseType:
    """Resolve a free-form category label or alias to the canonical ExpenseType."""

    if not raw_expense_type:
        raise ValueError("Тип траты не указан.")

    normalized = normalize_text(raw_expense_type)
    if normalized in EXPENSE_TYPE_LOOKUP:
        return EXPENSE_TYPE_LOOKUP[normalized]

    close_matches = get_close_matches(normalized, EXPENSE_TYPE_LOOKUP.keys(), n=1, cutoff=0.74)
    if close_matches:
        return EXPENSE_TYPE_LOOKUP[close_matches[0]]

    raise ValueError(f"Неизвестный тип траты: {raw_expense_type!r}")


def detect_explicit_expense_type(message: str) -> ExpenseType | None:
    """Try to detect an explicitly specified expense type inside a user message."""

    pattern = re.compile(
        r"(?:тип(?:\s+траты)?|категория|category|type)\s*[:=-]?\s*(?P<value>[^\n,.;]+)",
        re.IGNORECASE,
    )
    match = pattern.search(message)
    if match:
        try:
            return resolve_expense_type(match.group("value"))
        except ValueError:
            return None

    hashtags = re.findall(r"#([\wа-яА-ЯёЁ\-/ ]+)", message)
    for hashtag in hashtags:
        try:
            return resolve_expense_type(hashtag)
        except ValueError:
            continue

    normalized_message = normalize_text(message)
    expense_types = sorted(ExpenseType, key=lambda item: len(item.value), reverse=True)
    for expense_type in expense_types:
        normalized_value = normalize_text(expense_type.value)
        if normalized_message.startswith(f"{normalized_value}:"):
            return expense_type
        if normalized_message.startswith(f"{normalized_value} "):
            return expense_type

    return None


def build_categories_for_llm() -> list[ExpenseCategoryForLLM]:
    """Convert validated category metadata into an LLM-friendly serialized list."""

    return [
        ExpenseCategoryForLLM(
            code=expense_type.name,
            name=expense_type.value,
            description=EXPENSE_CATEGORIES[expense_type].description,
            aliases=list(EXPENSE_CATEGORIES[expense_type].aliases),
            examples=list(EXPENSE_CATEGORIES[expense_type].examples),
            inclusion_rules=list(EXPENSE_CATEGORIES[expense_type].inclusion_rules),
            exclusion_rules=list(EXPENSE_CATEGORIES[expense_type].exclusion_rules),
        )
        for expense_type in ExpenseType
    ]


if __name__ == "__main__":
    print(resolve_expense_type("кафе"))
    print(detect_explicit_expense_type("категория: продукты"))
    print(build_categories_for_llm()[:2])


