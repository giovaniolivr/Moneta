import io
import json

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.core.http import AuthenticatedHttpRequest
from apps.ledger.forms import TransactionFilterForm
from apps.ledger.models import Account, Transaction
from apps.ledger.selectors import filter_transactions
from apps.taxonomy.models import Category, Tag

from .schema import InvalidFileError
from .selectors import export_filename, export_payload, write_transactions_csv
from .services import MAX_FILE_BYTES, ImportFailedError, import_payload, parse_file

SESSION_KEY = "portability_import"
INDEX = "portability:index"


class ImportForm(forms.Form):
    file = forms.FileField(label="Arquivo de exportação (.json)")


def _attachment(content: str, content_type: str, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _render_index(
    request: AuthenticatedHttpRequest, form: ImportForm, errors: list[str] | None = None
) -> HttpResponse:
    user = request.user
    return render(
        request,
        "portability/index.html",
        {
            "form": form,
            "errors": errors or [],
            "max_mb": MAX_FILE_BYTES // (1024 * 1024),
            "counts": [
                ("Movimentações", Transaction.objects.for_user(user).count()),
                ("Contas", Account.objects.for_user(user).count()),
                ("Categorias", Category.objects.for_user(user).count()),
                ("Tags", Tag.objects.for_user(user).count()),
            ],
        },
    )


@require_GET
def index(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _render_index(request, ImportForm())


@require_GET
def export_json(request: AuthenticatedHttpRequest) -> HttpResponse:
    content = json.dumps(export_payload(request.user), ensure_ascii=False, indent=2)
    filename = export_filename("moneta", "json", timezone.localdate())
    return _attachment(content, "application/json; charset=utf-8", filename)


@require_GET
def transactions_csv(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Mesmos filtros (e mesma URL de parâmetros) da lista de movimentações."""
    tx_filter = TransactionFilterForm(request.GET, user=request.user).to_filter(
        timezone.localdate()
    )
    out = io.StringIO()
    write_transactions_csv(filter_transactions(request.user, tx_filter), out)
    filename = export_filename("moneta-movimentacoes", "csv", timezone.localdate())
    return _attachment(out.getvalue(), "text/csv; charset=utf-8", filename)


@require_POST
def import_preview(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Valida e simula a importação (nada é gravado); guarda o arquivo para a confirmação."""
    form = ImportForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_index(request, form)
    upload = form.cleaned_data["file"]
    if upload.size > MAX_FILE_BYTES:
        return _render_index(request, form, [f"Arquivo maior que {MAX_FILE_BYTES // 2**20} MB."])
    try:
        payload = parse_file(upload.read())
        report = import_payload(request.user, payload, dry_run=True)
    except InvalidFileError as exc:
        return _render_index(request, form, [str(exc)])
    except ImportFailedError as exc:
        return _render_index(request, form, exc.errors)
    request.session[SESSION_KEY] = {"name": upload.name, "payload": json.dumps(payload)}
    return render(request, "portability/preview.html", {"report": report, "filename": upload.name})


@require_POST
def import_confirm(request: AuthenticatedHttpRequest) -> HttpResponse:
    pending = request.session.pop(SESSION_KEY, None)
    if pending is None:
        messages.error(request, "Nenhuma importação pendente. Envie o arquivo novamente.")
        return redirect(INDEX)
    try:
        report = import_payload(request.user, json.loads(pending["payload"]))
    except (InvalidFileError, ImportFailedError) as exc:
        # Os dados mudaram entre a prévia e a confirmação: nada foi gravado.
        details = exc.errors if isinstance(exc, ImportFailedError) else [str(exc)]
        return _render_index(request, ImportForm(), details)
    messages.success(
        request,
        f"Importação concluída: {report.changes} alteraç{'ão' if report.changes == 1 else 'ões'}"
        f" ({report.transactions.created} movimentações novas).",
    )
    return redirect(INDEX)


@require_POST
def import_cancel(request: AuthenticatedHttpRequest) -> HttpResponse:
    request.session.pop(SESSION_KEY, None)
    messages.info(request, "Importação cancelada. Nada foi alterado.")
    return redirect(INDEX)
