from . import models
from . import report
from . import wizard


def pre_init_hook(env):
    env.cr.execute("ALTER TABLE pos_payment ADD COLUMN IF NOT EXISTS payment_reference varchar")
