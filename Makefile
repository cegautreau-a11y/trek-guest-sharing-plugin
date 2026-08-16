.PHONY: validate test package clean

validate:
	./scripts/validate.sh

test:
	python3 -m unittest discover -s tests -v

package:
	./scripts/package-release.sh

clean:
	rm -f dist/*.zip dist/SHA256SUMS
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
