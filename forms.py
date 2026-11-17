from __future__ import annotations

from django import forms
from django.utils import timezone

from .models import Address, RecurringTaskTemplate, Task
from .services import make_due_at, title_from_text


class TaskForm(forms.Form):
    DUE_SLOT_CHOICES = [
        ("noon", "До обяд"),
        ("eod", "До края на деня"),
    ]

    due_date = forms.DateField(
        label="Дата",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    client = forms.CharField(
        label="Клиент",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Изберете от адресната книга или въведете клиент",
                "autocomplete": "off",
            }
        ),
    )
    due_slot = forms.ChoiceField(
        label="Срок",
        choices=DUE_SLOT_CHOICES,
        initial="eod",
        widget=forms.RadioSelect,
    )
    type = forms.ChoiceField(label="Вид", choices=Task.Type.choices, initial=Task.Type.DELIVERY)
    contact_address = forms.CharField(
        label="Контакт и адрес",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Адрес, лице за контакт, телефон, вход/етаж..."}),
    )
    note = forms.CharField(
        label="Забележка",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Допълнителна инструкция към куриера"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.address_book = Address.objects.filter(is_active=True)
        self.selected_address = None
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
        client = cleaned.get("client", "").strip()
        if client:
            self.selected_address = self.address_book.filter(name__iexact=client).first()

        due_date = cleaned.get("due_date")
        if due_date and due_date < timezone.localdate():
            raise forms.ValidationError("Датата не може да бъде в миналото.")
        if not self.selected_address and not cleaned.get("contact_address", "").strip():
            self.add_error("contact_address", "Изберете клиент от адресната книга или въведете адрес.")
        return cleaned

    def save(self, commit=True):
        task_type = self.cleaned_data.get("type") or Task.Type.DELIVERY
        client = self.cleaned_data["client"].strip()
        contact_address = self.cleaned_data.get("contact_address", "").strip()
        if self.selected_address and not contact_address:
            contact_address = self.selected_address.address_text
        instance = Task(
            type=task_type,
            title=title_from_text(client, "Доставка" if task_type == Task.Type.DELIVERY else "Получаване"),
            description=client,
            customer_name=client,
            address=self.selected_address,
            address_text=contact_address,
            location_note=(self.cleaned_data.get("note") or "").strip()[:240],
            due_at=make_due_at(self.cleaned_data["due_date"], self.cleaned_data["due_slot"]),
            course_active=True,
        )
        if commit:
            instance.full_clean()
            instance.save()
        return instance


class CourseTaskIncompleteForm(forms.Form):
    note = forms.CharField(
        label="Причина",
        max_length=1000,
        widget=forms.Textarea(
            attrs={"rows": 2, "placeholder": "Защо задачата не е приключена?"}
        ),
    )


class RecurringTaskTemplateForm(forms.ModelForm):
    class Meta:
        model = RecurringTaskTemplate
        fields = ["task_text", "contact_address", "note", "type", "frequency", "weekday", "due_time", "is_active"]
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
