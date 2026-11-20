from __future__ import annotations

import logging

from django.db import models
from django.db.models.signals import pre_save

from home.image_utils import optimize_image_field


logger = logging.getLogger(__name__)


def optimize_model_images(instance) -> None:
    """Compress every not-yet-saved image upload on a model instance."""
    for field in instance._meta.fields:
        if not isinstance(field, (models.ImageField, models.FileField)):
            continue
        value = getattr(instance, field.name, None)
        if not value or not getattr(value, "name", ""):
            continue
        if getattr(value, "_committed", True):
            continue
        try:
            optimize_image_field(instance, field.name)
        except Exception:
            logger.exception("Unable to compress image field %s on %s", field.name, type(instance).__name__)


def _pre_save_optimize_images(sender, instance, **kwargs):
    optimize_model_images(instance)


def connect_image_optimizer(models_to_register) -> None:
    for model in models_to_register:
        pre_save.connect(
            _pre_save_optimize_images,
            sender=model,
            dispatch_uid=f"image_optimizer_{model._meta.label_lower}",
            weak=False,
        )
