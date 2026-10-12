import uuid
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.drawer import done, is_fragment, render_drawer
from apps.core.forms import apply_service_errors
from apps.core.http import AuthenticatedHttpRequest

from .forms import CategoryForm, TagAliasForm, TagMergeForm, TagRenameForm
from .models import Category, CategoryKind, Tag, TagAlias
from .selectors import categories_with_usage, list_categories, list_tags, tags_with_usage
from .services import (
    add_tag_alias,
    create_category,
    delete_category,
    delete_tag,
    merge_tags,
    remove_tag_alias,
    rename_tag,
    set_category_archived,
    update_category,
)

INDEX = "taxonomy:index"


def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    categories = list(categories_with_usage(request.user))
    return render(
        request,
        "taxonomy/index.html",
        {
            "category_groups": [
                (label, [c for c in categories if c.kind == kind and not c.is_archived])
                for kind, label in CategoryKind.choices
            ],
            "archived_categories": [c for c in categories if c.is_archived],
            "tags": tags_with_usage(request.user),
        },
    )


# Categorias ----------------------------------------------------------------------


def _category_drawer(
    request: AuthenticatedHttpRequest,
    form: CategoryForm,
    *,
    category: Category | None = None,
    action_error: str = "",
) -> HttpResponse:
    return render_drawer(
        request,
        "taxonomy/_category_form.html",
        {"form": form, "category": category, "action_error": action_error},
        title="Editar categoria" if category else "Nova categoria",
    )


@require_http_methods(["GET", "POST"])
def category_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            category = create_category(
                request.user,
                name=data["name"],
                kind=data["kind"],
                color=data["color"],
                icon=data["icon"],
            )
        except ValidationError as error:
            apply_service_errors(form, error)
        else:
            return done(request, f"Categoria “{category}” criada.", fallback=INDEX)
    return _category_drawer(request, form)


@require_http_methods(["GET", "POST"])
def category_update(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    category = get_object_or_404(list_categories(request.user, include_archived=True), pk=pk)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            update_category(category, name=data["name"], color=data["color"], icon=data["icon"])
        except ValidationError as error:
            apply_service_errors(form, error)
        else:
            return done(request, f"Categoria “{category}” atualizada.", fallback=INDEX)
    return _category_drawer(request, form, category=category)


@require_POST
def category_archive(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    category = get_object_or_404(list_categories(request.user, include_archived=True), pk=pk)
    set_category_archived(category, archived=not category.is_archived)
    verb = "arquivada" if category.is_archived else "restaurada"
    return done(request, f"Categoria “{category}” {verb}.", fallback=INDEX)


@require_POST
def category_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    category = get_object_or_404(list_categories(request.user, include_archived=True), pk=pk)
    name = str(category)
    try:
        delete_category(category)
    except ValidationError as error:
        return _category_drawer(
            request,
            CategoryForm(instance=category),
            category=category,
            action_error=error.messages[0],
        )
    return done(request, f"Categoria “{name}” excluída.", fallback=INDEX)


# Tags ---------------------------------------------------------------------------


def _tag_drawer(
    request: AuthenticatedHttpRequest,
    tag: Tag,
    *,
    rename_form: TagRenameForm | None = None,
    alias_form: TagAliasForm | None = None,
    merge_form: TagMergeForm | None = None,
    changed: bool = False,
    notice: str = "",
) -> HttpResponse:
    context: dict[str, Any] = {
        "tag": tag,
        "aliases": tag.aliases.order_by("normalized_alias"),
        "rename_form": rename_form or TagRenameForm(initial={"name": tag.name}),
        "alias_form": alias_form or TagAliasForm(),
        "merge_form": merge_form or TagMergeForm(user=request.user, source=tag),
        "notice": notice,
    }
    return render_drawer(
        request, "taxonomy/_tag_form.html", context, title=f"Tag #{tag.name}", changed=changed
    )


def _after_tag_change(request: AuthenticatedHttpRequest, tag: Tag, notice: str) -> HttpResponse:
    """Mudança que mantém o painel aberto; sem JS, volta para a página da tag."""
    if is_fragment(request):
        return _tag_drawer(request, tag, changed=True, notice=notice)
    messages.success(request, notice)
    return redirect(reverse("taxonomy:tag_edit", args=[tag.pk]))


@require_http_methods(["GET", "POST"])
def tag_edit(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    tag = get_object_or_404(list_tags(request.user), pk=pk)
    if request.method == "GET":
        return _tag_drawer(request, tag)
    form = TagRenameForm(request.POST)
    if form.is_valid():
        try:
            rename_tag(tag, form.cleaned_data["name"])
        except ValidationError as error:
            apply_service_errors(form, error)
        else:
            return _after_tag_change(request, tag, "Nome atualizado.")
    tag.refresh_from_db()
    return _tag_drawer(request, tag, rename_form=form)


@require_POST
def tag_alias_add(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    tag = get_object_or_404(list_tags(request.user), pk=pk)
    form = TagAliasForm(request.POST)
    if form.is_valid():
        try:
            add_tag_alias(tag, form.cleaned_data["alias"])
        except ValidationError as error:
            apply_service_errors(form, error)
        else:
            return _after_tag_change(request, tag, "Apelido adicionado.")
    return _tag_drawer(request, tag, alias_form=form)


@require_POST
def tag_alias_remove(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    alias = get_object_or_404(TagAlias.objects.for_user(request.user), pk=pk)
    tag = alias.tag
    remove_tag_alias(alias)
    return _after_tag_change(request, tag, "Apelido removido.")


@require_POST
def tag_merge(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    source = get_object_or_404(list_tags(request.user), pk=pk)
    form = TagMergeForm(request.POST, user=request.user, source=source)
    if form.is_valid():
        target = form.cleaned_data["target"]
        name = source.name
        merge_tags(source, target)
        return done(request, f"#{name} foi mesclada em #{target.name}.", fallback=INDEX)
    return _tag_drawer(request, source, merge_form=form)


@require_POST
def tag_delete(request: AuthenticatedHttpRequest, pk: uuid.UUID) -> HttpResponse:
    tag = get_object_or_404(list_tags(request.user), pk=pk)
    name = tag.name
    delete_tag(tag)
    return done(request, f"Tag #{name} excluída.", fallback=INDEX)
