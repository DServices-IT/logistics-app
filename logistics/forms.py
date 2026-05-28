from __future__ import annotations

from django import forms
from django.utils import timezone

from .models import RecurringTaskTemplate, Task
from .services import make_due_at, title_from_text


class TaskForm(forms.Form):
    DUE_SLOT_CHOICES = [
        ("noon", "До обяд"),
        ("eod", "До края на деня"),
    ]

    due_date = forms.DateField(
        label="Дата",
        initial=timezone.localdate,
        widget=forms.HiddenInput,
    )
    due_slot = forms.ChoiceField(
        label="Срок",
        choices=DUE_SLOT_CHOICES,
        initial="eod",
        widget=forms.RadioSelect,
    )
    type = forms.ChoiceField(label="Вид", choices=Task.Type.choices, initial=Task.Type.DELIVERY)
    urgency = forms.ChoiceField(label="Спешност", choices=Task.Urgency.choices, initial=Task.Urgency.NORMAL)
    task_text = forms.CharField(
        label="Задача",
        widget=forms.Textarea(attrs={"rows": 7, "placeholder": "Какво трябва да се достави/вземе?"}),
    )
    contact_address = forms.CharField(
        label="Контакт и адрес",
        widget=forms.Textarea(attrs={"rows": 7, "placeholder": "Адрес, лице за контакт, телефон, вход/етаж..."}),
    )
    note = forms.CharField(
        label="Забележка",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Допълнителна инструкция към куриера"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.fields:
            widget = self.fields[name].widget
            css = widget.attrs.get("class", "")
            if isinstance(widget, forms.Select):
                widget.attrs["class"] = (css + " form-select").strip()
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = (css + " form-check-input").strip()
            elif isinstance(widget, forms.RadioSelect):
                widget.attrs["class"] = "form-check-input"
            elif isinstance(widget, (forms.TextInput, forms.Textarea, forms.DateInput)):
                widget.attrs["class"] = (css + " form-control").strip()

    def clean(self):
        cleaned = super().clean()
        due_date = cleaned.get("due_date")
        if due_date and due_date < timezone.localdate():
            raise forms.ValidationError("Датата не може да бъде в миналото.")
        return cleaned

    def save(self, commit=True):
        task_type = self.cleaned_data.get("type") or Task.Type.DELIVERY
        task_text = self.cleaned_data["task_text"].strip()
        instance = Task(
            type=task_type,
            title=title_from_text(task_text, "Доставка" if task_type == Task.Type.DELIVERY else "Получаване"),
            description=task_text,
            address_text=self.cleaned_data["contact_address"].strip(),
            location_note=(self.cleaned_data.get("note") or "").strip()[:240],
            due_at=make_due_at(self.cleaned_data["due_date"], self.cleaned_data["due_slot"]),
            urgency=int(self.cleaned_data.get("urgency") or Task.Urgency.NORMAL),
        )
        if commit:
            instance.full_clean()
            instance.save()
        return instance


class RecurringTaskTemplateForm(forms.ModelForm):
    class Meta:
        model = RecurringTaskTemplate
        fields = ["task_text", "contact_address", "note", "type", "urgency", "frequency", "weekday", "due_time", "is_active"]
        widgets = {
            "task_text": forms.Textarea(attrs={"rows": 5, "placeholder": "Напр. Вземане на карти от Борика"}),
            "contact_address": forms.Textarea(attrs={"rows": 5, "placeholder": "Адрес, контакт, телефон..."}),
            "note": forms.Textarea(attrs={"rows": 2}),
            "due_time": forms.TextInput(attrs={"placeholder": "14:00"}),
        }
        labels = {
            "task_text": "Задача",
            "contact_address": "Контакт и адрес",
            "note": "Забележка",
            "type": "Вид",
            "urgency": "Спешност",
            "frequency": "Повторение",
            "weekday": "Ден от седмицата",
            "due_time": "Час",
            "is_active": "Активна",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_time"].initial = "14:00"
        self.fields["is_active"].initial = True
        self.fields["weekday"].required = False
        for name in self.fields:
            widget = self.fields[name].widget
            css = widget.attrs.get("class", "")
            if isinstance(widget, forms.Select):
                widget.attrs["class"] = (css + " form-select").strip()
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = (css + " form-check-input").strip()
            elif isinstance(widget, (forms.TextInput, forms.Textarea, forms.TimeInput)):
                widget.attrs["class"] = (css + " form-control").strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("frequency") == RecurringTaskTemplate.Frequency.WEEKLY and cleaned.get("weekday") is None:
            raise forms.ValidationError("Изберете ден от седмицата за седмична задача.")
        if cleaned.get("frequency") == RecurringTaskTemplate.Frequency.DAILY:
            cleaned["weekday"] = None
        return cleaned

