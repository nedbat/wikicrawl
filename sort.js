// A simple JS code to add sorting functionility to a HTML table:
// Ref: https://stackoverflow.com/questions/14267781/sorting-html-table-with-javascript
// getCellValue have been changed from the link above to relfect the dynamic type of table in space.

const getCellValue = (tr, idx) => {
    const cell = tr.children[idx];
    if (cell === undefined) {
        return false;
    }
    if (cell.children.length > 0) {
        if (cell.children[0].getAttribute("data")) {
            return cell.children[0].getAttribute("data");
        }
    }
    return cell.innerText || cell.textContent;
};


const comparer = (idx, asc) => (a, b) => (
    (v1, v2) => {
        if (v1 !== '' && v2 !== '' && !isNaN(v1) && !isNaN(v2)) {
            return v1 - v2;
        }
        else {
            return v1.toString().localeCompare(v2);
        }
    })(getCellValue(asc ? a : b, idx), getCellValue(asc ? b : a, idx));

// do the work...
document.querySelectorAll('th').forEach(
    th => th.addEventListener('click', (() => {
        const tbody = th.closest('table').querySelector('tbody');
        Array.from(tbody.querySelectorAll('tr'))
            .sort(comparer(Array.from(th.parentNode.children).indexOf(th), this.asc = !this.asc))
            .forEach(tr => tbody.appendChild(tr));
    }))
);
