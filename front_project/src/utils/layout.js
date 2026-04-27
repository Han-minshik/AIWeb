export const makeTab = (title = 'New Tab') => ({ id: Date.now() + Math.random(), title });
export const makeStack = () => { const t = makeTab(); return { tabs: [t], activeId: t.id }; };
export const makeStackWith = (tab) => ({ tabs: [tab], activeId: tab.id });
export const hasTabs = (s) => !!(s && s.tabs && s.tabs.length);


export function normalizeLayout(next) {
    const A = next.a, B = next.b, C = next.c, D = next.d;
    const aHas = hasTabs(A), bHas = hasTabs(B), cHas = hasTabs(C), dHas = hasTabs(D);


    const count = (aHas ? 1 : 0) + (bHas ? 1 : 0) + (cHas ? 1 : 0) + (dHas ? 1 : 0);
    if (count <= 1) {
        const only = aHas ? A : bHas ? B : cHas ? C : dHas ? D : makeStack();
        return { layout: '1', panes: { a: only, b: null, c: null, d: null } };
    }
 

    const leftList = [A, C].filter(hasTabs);
    const rightList = [B, D].filter(hasTabs);
    const leftCnt = leftList.length, rightCnt = rightList.length;


    if (count === 2) {
        const left = leftList[0] ?? rightList[0];
        const right = rightList[0] ?? leftList[1] ?? rightList[1];
        return { layout: '2', panes: { a: left, b: right, c: null, d: null } };
    }


    if (count === 3) {
        if (rightCnt === 2 && leftCnt === 1) {
            const leftSingle = leftList[0];
            const [rTop, rBot] = rightList;
            return { layout: '3L', panes: { a: leftSingle, b: rTop, c: rBot, d: null } };
        }
        const rightSingle = rightList[0];
        const [lTop, lBot] = leftList;
        return { layout: '3R', panes: { a: lTop, b: rightSingle, c: lBot, d: null } };
    }


    return { layout: '4', panes: { a: A, b: B, c: C, d: D } };
}