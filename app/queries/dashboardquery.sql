create or REPLACE FUNCTION get_dashboard_data () returns json as $$
declare
   output_json json;
begin
    SELECT
    json_build_object(
        'total_cattle',
        (
            SELECT
                count(*)
            FROM
                cattle_data
            WHERE
                new_is_currently_present = 1
        ),
        'total_female_cattle',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.gender) = 'female'
        ),
        'total_male_cattle',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.gender) = 'male'
        ),
        'total_female_calf',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.animal_type) = 'female calf'
        ),
        'total_male_calf',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.animal_type) = 'male calf'
        ),
        'total_milking_cow',
        (
            SELECT
                count(*)
            FROM
                cattle_data cd
            WHERE
                cd.new_is_currenlty_milking = 1
                AND cd.new_is_currently_present = 1
        ),
        'total_pregnant_cow',
        (
            SELECT
                count(*)
            FROM
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                AND cd.new_is_currently_pregnant = 1
        ),
        'source_breakdown',
        (
            SELECT
                json_agg(source_breakdown) as source_breakdown
            FROM
                (
                    SELECT
                        acquisition_type,
                        count(*) as total_cattle
                    FROM
                        cattle_data
                    WHERE
                        new_is_currently_present = 1
                    GROUP BY
                        acquisition_type
                    ORDER BY
                        total_cattle desc
                ) as source_breakdown
        ),
        'generation',
        (
            SELECT
                json_agg(gen)
            FROM
                (
                    select
                        sub.generation_level as generation,
                        count(*) as total_cattle
                    FROM
                        (
                            WITH RECURSIVE
                                cattle_lineage AS (
                                    -- 1. Anchor Member
                                    SELECT
                                        cd.*,
                                        1 AS generation_level
                                    FROM
                                        cattle_data cd
                                    WHERE
                                        cd.mother_tag_number IS NULL
                                        AND cd.father_tag_number IS NULL
                                    UNION ALL
                                    -- 2. Recursive Member
                                    SELECT
                                        child.*,
                                        parent.generation_level + 1 AS generation_level
                                    FROM
                                        cattle_data child
                                        INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                                )
                            SELECT
                                *
                            FROM
                                cattle_lineage
                        ) sub
                    GROUP by
                        sub.generation_level
                    ORDER by
                        sub.generation_level asc
                ) as gen
        ),
        'top_10_milking_cattle',
        (
            select
                json_agg(top_10)
            from
                (
                    WITH RECURSIVE
                        cattle_lineage AS (
                            -- 1. Anchor Member
                            SELECT
                                cd.*,
                                1 AS generation_level
                            FROM
                                cattle_data cd
                            WHERE
                                cd.mother_tag_number IS NULL
                                AND cd.father_tag_number IS NULL
                            UNION ALL
                            -- 2. Recursive Member
                            SELECT
                                child.*,
                                parent.generation_level + 1 AS generation_level
                            FROM
                                cattle_data child
                                INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                        )
                    SELECT
                        cl.tag_number,
                        cl.name,
                        cl.generation_level AS generation,
                        round(top_milk.total_milk) as "total_milk"
                    FROM
                        cattle_lineage AS cl
                        INNER JOIN (
                            -- Your top 10 milk subquery
                            SELECT
                                cml.tag_number,
                                SUM(cml.milk) AS total_milk
                            FROM
                                public.cattle_milk_logs AS cml
                            GROUP BY
                                cml.tag_number
                            ORDER BY
                                total_milk DESC
                            LIMIT
                                10
                        ) AS top_milk ON cl.tag_number = top_milk.tag_number
                    ORDER BY
                        top_milk.total_milk DESC
                ) as top_10
        ),
        'top_10_fit_cattle',
        (
            select
                json_agg(physical_good)
            from
                (
                    WITH RECURSIVE
                        cattle_lineage AS (
                            -- 1. Anchor Member (The First Generation)
                            SELECT
                                cd.*,
                                1 AS generation_level
                            FROM
                                cattle_data cd
                            WHERE
                                cd.mother_tag_number IS NULL
                                AND cd.father_tag_number IS NULL
                            UNION ALL
                            -- 2. Recursive Member
                            SELECT
                                child.*,
                                parent.generation_level + 1 AS generation_level
                            FROM
                                cattle_data child
                                INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                        )
                    SELECT
                        cl.tag_number,
                        cl.name,
                        cl.generation_level AS generation,
                        top_scores.hip_width,
                        top_scores.total_score
                    FROM
                        cattle_lineage AS cl
                        INNER JOIN (
                            -- Your top 10 scoring subquery
                            SELECT
                                tag_number,
                                hip_width,
                                (
                                    COALESCE(head, 0) + COALESCE(ear, 0) + COALESCE(eye, 0) + COALESCE(muzzle, 0) + COALESCE(horn, 0) + COALESCE(skin, 0) + COALESCE(tail, 0) + COALESCE(hump, 0) + COALESCE(udder, 0) + COALESCE(teat, 0) + COALESCE(dewlap, 0) + COALESCE(milk_vein, 0)
                                ) AS total_score
                            FROM
                                cattle_physical_logs
                            ORDER BY
                                total_score DESC
                            LIMIT
                                10
                        ) AS top_scores ON cl.tag_number = top_scores.tag_number
                    ORDER BY
                        top_scores.total_score DESC
                ) physical_good
        ),
        'month_wise_milk_production',
        (
            select json_agg(monthwisemilk) as monthwisemilk
            from (
                SELECT 
                    TO_CHAR(date, 'YYYY-MM') AS month,
                    round(SUM(milk)) AS total_milk
                FROM 
                    cattle_milk_logs
                GROUP BY 
                    TO_CHAR(date, 'YYYY-MM')
                ORDER BY 
                    month ASC
            ) as monthwisemilk
        )
    ) into output_json;

    return output_json;

end;
$$ language plpgsql;

CREATE OR REPLACE FUNCTION get_nested_cattle_tree (target_tag TEXT) RETURNS JSON AS $$
DECLARE
    tree_json JSON;
BEGIN
    SELECT json_build_object(
        'tag_number', c.tag_number,
        'name', c.name,
        'date_of_birth', c.date_of_birth,
        -- The recursive part: It calls itself to find and nest the next generation
        'children', (
            SELECT COALESCE(json_agg(get_nested_cattle_tree(child.tag_number)), '[]'::json)
            FROM cattle_data child
            WHERE child.mother_tag_number = c.tag_number 
               OR child.father_tag_number = c.tag_number
        )
    ) INTO tree_json
    FROM cattle_data c
    WHERE c.tag_number = target_tag;

    RETURN tree_json;
END;
$$ LANGUAGE plpgsql;