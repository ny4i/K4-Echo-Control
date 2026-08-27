#!/usr/bin/env bash
# Package the Alexa skill Lambda into a deployable zip.
#
#   ./tools/build_lambda.sh            -> dist/k4-echo-lambda.zip
#
# The zip carries only what the Lambda needs: the handler plus the four shared
# modules. The bridge-only modules stay out, and no third-party dependency is
# bundled -- boto3 is already present in the Lambda runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${ROOT}/build/lambda"
DIST="${ROOT}/dist"
ZIP="${DIST}/k4-echo-lambda.zip"

LAMBDA_MODULES=(__init__.py alexa.py commands.py signing.py transports.py)

rm -rf "${BUILD}"
mkdir -p "${BUILD}/k4echo" "${DIST}"

cp "${ROOT}/lambda/lambda_function.py" "${BUILD}/"
for module in "${LAMBDA_MODULES[@]}"; do
    cp "${ROOT}/k4echo/${module}" "${BUILD}/k4echo/"
done

# Fail loudly rather than shipping a zip that will ImportError at runtime.
( cd "${BUILD}" && python3 -c "
import importlib, sys
sys.path.insert(0, '.')
for name in ('lambda_function', 'k4echo.alexa', 'k4echo.commands', 'k4echo.signing', 'k4echo.transports'):
    importlib.import_module(name)
print('import check passed')
" )

rm -f "${ZIP}"
( cd "${BUILD}" && zip -qr "${ZIP}" . -x '*.pyc' -x '__pycache__/*' -x '*/__pycache__/*' )

echo "built ${ZIP}"
unzip -l "${ZIP}" | tail -n +4 | head -n -2
echo
echo "deploy with:"
echo "  aws lambda update-function-code --function-name k4-echo-control --zip-file fileb://${ZIP}"
